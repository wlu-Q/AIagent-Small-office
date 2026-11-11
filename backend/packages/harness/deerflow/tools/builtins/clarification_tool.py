'''Model-triggered clarification tool handled by ClarificationMiddleware.'''

from typing import Literal

from langchain.tools import tool


@tool('ask_clarification', parse_docstring=True)
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        'missing_info',
        'ambiguous_requirement',
        'approach_choice',
    ],
    options: list[str] | None = None,
) -> str:
    '''Ask one blocking question only when critical user input is missing.

    Do not call this tool when the request is already actionable. Do not use it
    for permissions, approvals, routine planning, or recommendations. When
    choices are useful, provide two to four short options; free text remains
    available even when choices are present.

    Args:
        question: One concise question whose answer is required to continue.
        clarification_type: Why the current request cannot safely continue.
        options: Optional list containing two to four valid choices.
    '''
    return 'Clarification request is handled by middleware'
