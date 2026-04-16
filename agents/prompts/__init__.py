from agents.prompts.modeler import MODELER_PROMPT
from agents.prompts.coder import CODER_PROMPT
from agents.prompts.writer import get_writer_section_prompts, MCM_LATEX_TEMPLATE
from agents.prompts.shared import ALGORITHM_GUIDE, MODEL_COMBINATIONS, VIZ_COLORS

__all__ = [
    "MODELER_PROMPT",
    "CODER_PROMPT",
    "get_writer_section_prompts",
    "MCM_LATEX_TEMPLATE",
    "ALGORITHM_GUIDE",
    "MODEL_COMBINATIONS",
    "VIZ_COLORS",
]
