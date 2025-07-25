import json
import logging
import os
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from rank_llm.data import Request, Result
from rank_llm.rerank.inference_handler import BaseInferenceHandler

logger = logging.getLogger(__name__)


# TODO(issue #236): Need to remove this after all the inference handlers are added
class PromptMode(Enum):
    UNSPECIFIED = "unspecified"
    RANK_GPT = "rank_GPT"
    RANK_GPT_APEER = "rank_GPT_APEER"
    LRL = "LRL"
    MONOT5 = "monot5"
    DUOT5 = "duot5"
    LiT5 = "LiT5"

    def __str__(self):
        return self.value


class RankLLM(ABC):
    def __init__(
        self,
        model: str,
        context_size: int,
        prompt_mode: Optional[PromptMode] = None,
        prompt_template_path: Optional[str] = None,
        num_few_shot_examples: int = 0,
        few_shot_file: Optional[str] = None,
    ) -> None:
        self._model = model
        self._context_size = context_size
        self._prompt_mode = prompt_mode
        self._num_few_shot_examples = num_few_shot_examples
        self._few_shot_file = few_shot_file

        if prompt_mode:
            print(
                "PromptMode is deprecated and will be removed in v0.30.0. Please use the prompt_template_path argument with a valid template file instead."
            )

        try:
            if not isinstance(prompt_template_path, (str, os.PathLike)):
                raise TypeError(
                    f"Expected str or PathLike, got {type(prompt_template_path)}"
                )
            print(f"Using prompt template: {prompt_template_path}")
            with open(prompt_template_path, "r") as file:
                data = yaml.safe_load(file)

                self._inference_handler = self._create_handler(data)
                print(f"Successfully created {data['method']} inference handler!")
        except FileNotFoundError:
            raise ValueError("Prompt template file missing or not found")

        if self._num_few_shot_examples > 0:
            if not few_shot_file:
                raise ValueError(
                    "few_shot_examples_file must be provided when num_few_shot_examples > 0"
                )
            self._load_few_shot_examples(few_shot_file)
        else:
            self._examples = []

    @abstractmethod
    def run_llm_batched(
        self, prompts: List[Union[str, List[Dict[str, str]]]], **kwargs
    ) -> List[Tuple[str, int]]:
        """
        Abstract method to run the target language model with a batch of prompts.

        Args:
            prompts (List[Union[str, List[Dict[str, str]]]): The list of prompts to be processed by the model.

        Returns:
            List[Tuple[str, int]]: A list of tuple objects containing the text responses and the number of tokens in the responses.
        """
        pass

    @abstractmethod
    def run_llm(
        self, prompt: Union[str, List[Dict[str, str]]], **kwargs
    ) -> Tuple[str, int]:
        """
        Abstract method to run the target language model with a passed in prompt.

        Args:
            prompt (Union[str, List[Dict[str, str]]]): The prompt to be processed by the model.

        Returns:
            Tuple[str, int]: A tuple object containing the text response and the number of tokens in the response.
        """
        pass

    @abstractmethod
    def create_prompt_batched(
        self, results: List[Result], rank_start: int, rank_end: int, batch_size: int
    ) -> List[Tuple[Union[str, List[Dict[str, str]]], int]]:
        """
        Abstract method to create a batch of prompts based on the results and given ranking range.

        Args:
            results (List[Result]): The list of result objects containing data for prompt generation.
            rank_start (int): The starting rank for prompt generation.
            rank_end (int): The ending rank for prompt generation.

        Returns:
            Tuple[List[Union[str, List[Dict[str, str]]], List[int]]: A tuple object containing the list of generated prompts and the list of number of tokens in the generated prompts.
        """
        pass

    @abstractmethod
    def create_prompt(
        self, result: Result, rank_start: int, rank_end: int
    ) -> Tuple[Union[str, List[Dict[str, str]]], int]:
        """
        Abstract method to create a prompt based on the result and given ranking range.

        Args:
            result (Result): The result object containing data for prompt generation.
            rank_start (int): The starting rank for prompt generation.
            rank_end (int): The ending rank for prompt generation.

        Returns:
            Tuple[Union[str, List[Dict[str, str]]], int]: A tuple object containing the generated prompt and the number of tokens in the generated prompt.
        """
        pass

    @abstractmethod
    def get_num_tokens(self, prompt: Union[str, List[Dict[str, str]]]) -> int:
        """
        Abstract method to calculate the number of tokens contained in the given prompt.

        Args:
            prompt (Union[str, List[Dict[str, str]]]): The prompt for which to compute the token count for.

        Returns:
            int: The number of tokens in the given prompt.
        """
        pass

    @abstractmethod
    def cost_per_1k_token(self, input_token: bool) -> float:
        """
        Abstract method to calculate the cost per 1,000 tokens for the target language model.

        Args:
            input_token (bool): Flag to indicate if the cost is for input tokens or output tokens.

        Returns:
            float: The cost per 1,000 tokens.
        """
        pass

    @abstractmethod
    def num_output_tokens(self) -> int:
        """
        Abstract method to estimate the number of tokens in the model's output, constrained by max tokens for the target language model.

        Returns:
            int: The estimated number of output tokens.
        """
        pass

    @abstractmethod
    def rerank_batch(
        self,
        requests: List[Request],
        rank_start: int = 0,
        rank_end: int = 100,
        shuffle_candidates: bool = False,
        logging: bool = False,
        **kwargs: Any,
    ) -> List[Result]:
        """
        Reranks a list of requests using the RankLLM model_coordinator.

        This function applies a sliding window algorithm to rerank the results.
        Each window of results is processed by the RankLLM model_coordinator to obtain a new ranking.

        Args:
            requests (List[Request]): The list of requests. Each request has a query and a candidates list.
            rank_start (int, optional): The starting rank for processing. Defaults to 0.
            rank_end (int, optional): The end rank for processing. Defaults to 100.
            window_size (int, optional): The size of each sliding window. Defaults to 20.
            stride (int, optional): The stride size for moving the window. Defaults to 10.
            shuffle_candidates (bool, optional): Whether to shuffle candidates before reranking. Defaults to False.
            logging (bool, optional): Enables logging of the reranking process. Defaults to False.
            batched (bool, optional): Whether to use batched processing. Defaults to False.

        Returns:
            List[Result]: A list containing the reranked candidates.
        """
        pass

    @abstractmethod
    def get_output_filename(
        self,
        top_k_candidates: int,
        dataset_name: str,
        shuffle_candidates: bool,
        **kwargs: Any,
    ) -> str:
        """
        Returns the output filename used when writing rerank results to file
        """
        pass

    def _create_handler(self, template: Dict[str, str]) -> BaseInferenceHandler:
        # TODO(issue #236 and #237): Need to modify function to select correct inference handler
        from rank_llm.rerank.listwise.multiturn_listwise_inference_handler import (
            MultiTurnListwiseInferenceHandler,
        )
        from rank_llm.rerank.listwise.rankfid_inference_handler import (
            RankFIDInferenceHandler,
        )
        from rank_llm.rerank.listwise.singleturn_listwise_inference_handler import (
            SingleTurnListwiseInferenceHandler,
        )
        from rank_llm.rerank.pairwise.pairwise_inference_handler import (
            PairwiseInferenceHandler,
        )
        from rank_llm.rerank.pointwise.pointwise_inference_handler import (
            PointwiseInferenceHandler,
        )

        try:
            if template["method"] == "singleturn_listwise":
                return SingleTurnListwiseInferenceHandler(template)
            elif template["method"] == "multiturn_listwise":
                return MultiTurnListwiseInferenceHandler(template)
            elif template["method"] == "rankfid":
                return RankFIDInferenceHandler(template)
            elif template["method"] == "pointwise":
                return PointwiseInferenceHandler(template)
            elif template["method"] == "pairwise":
                return PairwiseInferenceHandler(template)
            else:
                raise ValueError("Invalid template method")
        except:
            raise ValueError("Please provide a method section in the template")

    def _load_few_shot_examples(self, file_path: str):
        try:
            with open(file_path, "r") as json_file:
                self._examples = json.load(json_file)
        except FileNotFoundError:
            raise ValueError(f"Few-shot examples file not found: {file_path}")
        except json.JSONDecodeError:
            raise ValueError(
                f"Invalid JSON format in few-shot examples file: {file_path}"
            )
