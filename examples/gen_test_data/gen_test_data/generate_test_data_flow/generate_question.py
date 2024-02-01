from typing import Union

from utils import llm_call

from promptflow import tool
from promptflow.connections import AzureOpenAIConnection, OpenAIConnection


@tool
def generate_seed_question(
    connection: Union[OpenAIConnection, AzureOpenAIConnection],
    model_or_deployment_name: str,
    generate_question_prompt: str,
    context: str = None,
    temperature: float = 1.0,
    max_tokens: int = 512,
):
    """
    Generates a question based on the given context.

    Returns:
        dict: The generated seed question.
    """
    # text trunk is not valid, just skip test data gen.
    if not context:
        return ""

    seed_question = llm_call(
        connection, model_or_deployment_name, generate_question_prompt, temperature=temperature, max_tokens=max_tokens
    )
    return seed_question