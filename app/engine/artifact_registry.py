"""
NapsterTec AI - Central Artifact Registry
Module: app/engine/artifact_registry.py
"""
import logging
from typing import Dict, List, Optional
from app.schemas.shared_artifacts import BaseArtifact

logger = logging.getLogger(__name__)

class ArtifactRegistry:
    def __init__(self):
        # In-memory index: lead_id -> { artifact_type -> [artifacts] }
        self._index: Dict[str, Dict[str, List[BaseArtifact]]] = {}

    def validate(self, artifact: BaseArtifact) -> bool:
        """Strict validation before registration is allowed."""
        try:
            if not artifact.artifact_id: return False
            if not artifact.artifact_type: return False
            if not artifact.lead_id: return False
            # Ensures it's a valid Pydantic model representation
            artifact.model_validate(artifact.model_dump())
            return True
        except Exception as e:
            logger.error(f"[ArtifactRegistry] Validation failed: {e}")
            return False

    def register(self, artifact: BaseArtifact) -> bool:
        """Registers a validated artifact for discovery by other agents."""
        if not self.validate(artifact):
            logger.error(f"Failed to register invalid artifact {getattr(artifact, 'artifact_id', 'Unknown')}")
            return False
            
        lead_id = artifact.lead_id
        a_type = artifact.artifact_type
        
        if lead_id not in self._index:
            self._index[lead_id] = {}
        if a_type not in self._index[lead_id]:
            self._index[lead_id][a_type] = []
            
        self._index[lead_id][a_type].append(artifact)
        logger.info(f"[ArtifactRegistry] Registered {a_type} ({artifact.artifact_id}) for Lead {lead_id} at v{artifact.version}")
        return True

    def get_latest(self, lead_id: str, artifact_type: str) -> Optional[BaseArtifact]:
        """Discovery mechanism for future agents to consume intelligence."""
        artifacts = self._index.get(lead_id, {}).get(artifact_type, [])
        if not artifacts:
            return None
        return sorted(artifacts, key=lambda a: a.version, reverse=True)[0]

# Global Singleton Registry
artifact_registry = ArtifactRegistry()