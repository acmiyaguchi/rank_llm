import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import openai
from openai import OpenAI
import tiktoken
from tqdm import tqdm

from rank_llm.data import Request, Result
from rank_llm.rerank.rankllm import PromptMode

from .listwise_rankllm import ListwiseRankLLM

class SafeOpenaiBackend(ListwiseRankLLM):
    def __init__(
        self,
        model: str,
        context_size: int,
        prompt_mode: Optional[PromptMode] = None,
        prompt_template_path: Optional[str] = None,
        num_few_shot_examples: int = 0,
        few_shot_file: Optional[str] = None,
        window_size: int = 20,
        keys=None,
        key_start_id=None,
        proxy=None,
        api_base: Optional[str] = None,
        openrouter_config: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Creates instance of the SafeOpenaiBackend class, a specialized version of RankLLM designed for safely handling OpenAI API calls with
        support for key cycling, proxy configuration, Azure AI conditional integration, and OpenRouter support.

        Parameters:
        - model (str): The model identifier for the LLM (model identifier information can be found via OpenAI's model lists).
        - context_size (int): The maximum number of tokens that the model can handle in a single request.
        - prompt_mode (PromptMode, optional): Specifies the mode of prompt generation, with the default set to RANK_GPT,
         indicating that this class is designed primarily for listwise ranking tasks following the RANK_GPT methodology.
        - num_few_shot_examples (int, optional): Number of few-shot learning examples to include in the prompt, allowing for
        the integration of example-based learning to improve model performance. Defaults to 0, indicating no few-shot examples
        by default.
        - window_size (int, optional): The window size for handling text inputs. Defaults to 20.
        - keys (Union[List[str], str], optional): A list of OpenAI API keys or a single OpenAI API key.
        - key_start_id (int, optional): The starting index for the OpenAI API key cycle.
        - proxy (str, optional): The proxy configuration for OpenAI API calls.
        - api_type (str, optional): The type of API service, if using Azure AI as the backend.
        - api_base (str, optional): The base URL for the API, applicable when using Azure AI or custom endpoints like OpenRouter.
        - api_version (str, optional): The API version, necessary for Azure AI integration.
        - openrouter_config (Dict[str, str], optional): Configuration for OpenRouter API including:
            - 'site_url': Your site URL for rankings on openrouter.ai (optional)
            - 'site_name': Your site name for rankings on openrouter.ai (optional)

        Raises:
        - ValueError: If an unsupported prompt mode is provided or if no OpenAI API keys / invalid OpenAI API keys are supplied.

        Note:
        - This class supports cycling between multiple OpenAI API keys to distribute quota usage or handle rate limiting.
        - Azure AI integration depends on the presence of `api_type`, `api_base`, and `api_version`.
        - OpenRouter integration is enabled when `api_base` points to OpenRouter and optional `openrouter_config` is provided.
        """
        if isinstance(keys, str):
            keys = [keys]
        if not keys:
            raise ValueError("Please provide OpenAI Keys.")

        if prompt_mode and prompt_mode not in [
            PromptMode.RANK_GPT,
            PromptMode.RANK_GPT_APEER,
            PromptMode.LRL,
        ]:
            raise ValueError(
                f"unsupported prompt mode for GPT models: {prompt_mode}, expected {PromptMode.RANK_GPT}, {PromptMode.RANK_GPT_APEER} or {PromptMode.LRL}."
            )

        if prompt_template_path is None:
            if prompt_mode == PromptMode.RANK_GPT:
                prompt_template_path = (
                    "src/rank_llm/rerank/prompt_templates/rank_gpt_template.yaml"
                )
            elif prompt_mode == PromptMode.RANK_GPT_APEER:
                prompt_template_path = (
                    "src/rank_llm/rerank/prompt_templates/rank_gpt_apeer_template.yaml"
                )
            else:
                prompt_template_path = (
                    "src/rank_llm/rerank/prompt_templates/rank_lrl_template.yaml"
                )
        super().__init__(
            model=model,
            context_size=context_size,
            prompt_mode=prompt_mode,
            prompt_template_path=prompt_template_path,
            num_few_shot_examples=num_few_shot_examples,
            few_shot_file=few_shot_file,
            window_size=window_size,
        )

        self._output_token_estimate = None
        self._keys = keys
        self._cur_key_id = key_start_id or 0
        self._cur_key_id = self._cur_key_id % len(self._keys)
        self.openrouter_config = openrouter_config or {}
        
        # Initialize OpenAI client
        client_kwargs = {
            "api_key": self._keys[self._cur_key_id]
        }
        
        if proxy:
            client_kwargs["http_client"] = openai.DefaultHttpxClient(proxies=proxy)

        if api_base:
            # Custom API base (e.g., OpenRouter)
            client_kwargs["base_url"] = api_base
            
            # Add OpenRouter headers if config is provided
            if self.openrouter_config and "openrouter.ai" in api_base:
                headers = {}
                if "site_url" in self.openrouter_config:
                    headers["HTTP-Referer"] = self.openrouter_config["site_url"]
                if "site_name" in self.openrouter_config:
                    headers["X-Title"] = self.openrouter_config["site_name"]
                    
                if headers:
                    client_kwargs["default_headers"] = headers
            
        self.client = OpenAI(**client_kwargs)

    class CompletionMode(Enum):
        UNSPECIFIED = 0
        CHAT = 1
        TEXT = 2

    def rerank_batch(
        self,
        requests: List[Request],
        rank_start: int = 0,
        rank_end: int = 100,
        shuffle_candidates: bool = False,
        logging: bool = False,
        **kwargs: Any,
    ) -> List[Result]:
        top_k_retrieve: int = kwargs.get("top_k_retrieve", rank_end)
        rank_end = min(top_k_retrieve, rank_end)
        window_size: int = kwargs.get("window_size", 20)
        window_size = min(window_size, top_k_retrieve)
        stride: int = kwargs.get("stride", 10)
        populate_invocations_history: bool = kwargs.get(
            "populate_invocations_history", False
        )
        results = []
        for request in tqdm(requests):
            result = self.sliding_windows(
                request,
                rank_start=max(rank_start, 0),
                rank_end=min(rank_end, len(request.candidates)),
                window_size=window_size,
                stride=stride,
                shuffle_candidates=shuffle_candidates,
                logging=logging,
                populate_invocations_history=populate_invocations_history,
            )
            results.append(result)
        return results

    def _call_completion(
        self,
        *args,
        completion_mode: CompletionMode,
        return_text=False,
        reduce_length=False,
        **kwargs,
    ) -> Union[str, Dict[str, Any]]:
        for i in range(3): # Retry up to 3 times
            try:
                if completion_mode == self.CompletionMode.CHAT:
                    completion_kwargs = {**kwargs, "timeout": 30}
                    completion = self.client.chat.completions.create(
                        *args, **completion_kwargs
                    )
                elif completion_mode == self.CompletionMode.TEXT:
                    completion = self.client.completions.create(*args, **kwargs)
                else:
                    raise ValueError(
                        "Unsupported completion mode: %V" % completion_mode
                    )
                if return_text:
                    completion = (
                        completion.choices[0].message.content
                        if completion_mode == self.CompletionMode.CHAT
                        else completion.choices[0].text
                    )
                    # if completion has 0 length, retry request
                    if len(completion) == 0:
                        print("Empty completion, retrying...")
                        raise Exception("Empty completion")
                break
            except Exception as e:
                print("Error in completion call")
                print(str(e))
                if "This model's maximum context length is" in str(e):
                    print("reduce_length")
                    return "ERROR::reduce_length"
                if "The response was filtered" in str(e):
                    print("The response was filtered")
                    return "ERROR::The response was filtered"
                time.sleep(0.1)
        return completion

    def run_llm(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        current_window_size: Optional[int] = None,
    ) -> Tuple[str, int]:
        model_key = "model"
        response = self._call_completion(
            messages=prompt,
            temperature=0,
            completion_mode=SafeOpenaiBackend.CompletionMode.CHAT,
            return_text=True,
            **{model_key: self._model},
        )
        try:
            encoding = tiktoken.get_encoding(self._model)
        except:
            encoding = tiktoken.get_encoding("cl100k_base")
        return response, len(encoding.encode(response))

    def num_output_tokens(self, current_window_size: Optional[int] = None) -> int:
        if current_window_size is None:
            current_window_size = self._window_size
        if self._output_token_estimate and self._window_size == current_window_size:
            return self._output_token_estimate
        else:
            try:
                encoder = tiktoken.get_encoding(self._model)
            except:
                encoder = tiktoken.get_encoding("cl100k_base")

            _output_token_estimate = (
                len(
                    encoder.encode(
                        " > ".join([f"[{i+1}]" for i in range(current_window_size)])
                    )
                )
                - 1
            )
            if (
                self._output_token_estimate is None
                and self._window_size == current_window_size
            ):
                self._output_token_estimate = _output_token_estimate
            return _output_token_estimate

    def create_prompt_batched(self):
        pass

    def run_llm_batched(self):
        pass

    def create_prompt(
        self, result: Result, rank_start: int, rank_end: int
    ) -> Tuple[List[Dict[str, str]], int]:
        max_length = 300 * (self._window_size // (rank_end - rank_start))

        while True:
            prompt = self._inference_handler.generate_prompt(
                result=result,
                rank_start=rank_start,
                rank_end=rank_end,
                max_length=max_length,
                num_fewshot_examples=self._num_few_shot_examples,
                fewshot_examples=self._examples,
            )
            num_tokens = self.get_num_tokens(prompt)
            if num_tokens <= self.max_tokens() - self.num_output_tokens():
                break
            else:
                max_length -= max(
                    1,
                    (num_tokens - self.max_tokens() + self.num_output_tokens())
                    // ((rank_end - rank_start) * 4),
                )

        return prompt, num_tokens

    def get_num_tokens(self, prompt: Union[str, List[Dict[str, str]]]) -> int:
        """Returns the number of tokens used by a list of messages in prompt."""
        if self._model in ["gpt-3.5-turbo-0301", "gpt-3.5-turbo"]:
            tokens_per_message = (
                4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
            )
            tokens_per_name = -1  # if there's a name, the role is omitted
        elif self._model in ["gpt-4-0314", "gpt-4"]:
            tokens_per_message = 3
            tokens_per_name = 1
        else:
            tokens_per_message, tokens_per_name = 0, 0

        try:
            encoding = tiktoken.get_encoding(self._model)
        except:
            encoding = tiktoken.get_encoding("cl100k_base")

        num_tokens = 0
        if isinstance(prompt, list):
            for message in prompt:
                num_tokens += tokens_per_message
                for key, value in message.items():
                    num_tokens += len(encoding.encode(value))
                    if key == "name":
                        num_tokens += tokens_per_name
        else:
            num_tokens += len(encoding.encode(prompt))
        num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
        return num_tokens

    def cost_per_1k_token(self, input_token: bool) -> float:
        # TODO
        return 0

    def get_name(self) -> str:
        return self._model
