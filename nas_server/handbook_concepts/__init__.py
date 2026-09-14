"""Concept-page modules register here as content is implemented. Not every
page is Group A -- a conceptual/methodology page from a later group (e.g.
Group B's P04 "input data state") reuses this same registry rather than the
tool-recipe-shaped HandbookArticle, when its own research says so."""

from nas_server.handbook_contract import ConceptArticle
from nas_server.handbook_concepts.calibration_foundations import (
    CONCEPT_ARTICLE as _CALIBRATION_FOUNDATIONS,
)
from nas_server.handbook_concepts.experiment_evidence_aggregation import (
    CONCEPT_ARTICLE as _EXPERIMENT_EVIDENCE_AGGREGATION,
)
from nas_server.handbook_concepts.experiment_mode import CONCEPT_ARTICLE as _EXPERIMENT_MODE
from nas_server.handbook_concepts.final_image_quality import (
    CONCEPT_ARTICLE as _FINAL_IMAGE_QUALITY,
)
from nas_server.handbook_concepts.how_nova_knows import CONCEPT_ARTICLE as _HOW_NOVA_KNOWS
from nas_server.handbook_concepts.input_data_state import (
    CONCEPT_ARTICLE as _INPUT_DATA_STATE,
)
from nas_server.handbook_concepts.knowledge_integration import (
    CONCEPT_ARTICLE as _KNOWLEDGE_INTEGRATION,
)
from nas_server.handbook_concepts.measurement_framework import (
    CONCEPT_ARTICLE as _MEASUREMENT_FRAMEWORK,
)
from nas_server.handbook_concepts.output_reproducibility import (
    CONCEPT_ARTICLE as _OUTPUT_REPRODUCIBILITY,
)
from nas_server.handbook_concepts.target_morphology_strategy import (
    CONCEPT_ARTICLE as _TARGET_MORPHOLOGY_STRATEGY,
)

# Upcoming content issues each add one module and one import here. Keeping the
# registry empty (until now) made the publication hold explicit and testable.
CONCEPT_ARTICLES: tuple[ConceptArticle, ...] = (
    _HOW_NOVA_KNOWS,
    _EXPERIMENT_MODE,
    _MEASUREMENT_FRAMEWORK,
    _FINAL_IMAGE_QUALITY,
    _EXPERIMENT_EVIDENCE_AGGREGATION,
    _KNOWLEDGE_INTEGRATION,
    _INPUT_DATA_STATE,
    _CALIBRATION_FOUNDATIONS,
    _TARGET_MORPHOLOGY_STRATEGY,
    _OUTPUT_REPRODUCIBILITY,
)
