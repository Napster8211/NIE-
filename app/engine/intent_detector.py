import logging
import re
from typing import List, Any, Optional

from app.engine.models import Intent, Capability

logger = logging.getLogger(__name__)


class IntentDetector:
    def __init__(self):
        # Heuristic rules matching keywords to capabilities
        self.capability_signatures = {
            Capability.CODING: [
                r"\bcode\b",
                r"\bpython\b",
                r"\bfunction\b",
                r"\brepos?\b",
                r"\bdebug\b",
                r"\brefactor\b",
                r"\bapi\b",
                r"\bbackend\b",
                r"\bfrontend\b",
                r"\bjavascript\b",
                r"\btypescript\b",
                r"\breact\b",
                r"\bfastapi\b",
            ],
            Capability.SYSTEM_INSPECTION: [
                r"\binspect\b",
                r"\bhardware\b",
                r"\bos\b",
                r"\bnetwork\b",
                r"\baudit\b",
                r"\bsystem\b",
                r"\bprocess\b",
                r"\bsecurity\b",
                r"\bexploit\b",
                r"\bvulnerability\b",
            ],
            Capability.VISION: [
                r"\bimage\b",
                r"\bpicture\b",
                r"\blook at\b",
                r"\bvisual\b",
                r"\bphoto\b",
                r"\bscreenshot\b",
                r"\banalyze this image\b",
                r"\bwhat is in this image\b",
            ],
            Capability.RESEARCH: [
                r"\bsearch\b",
                r"\bfind out\b",
                r"\bhistory of\b",
                r"\bresearch\b",
                r"\bcompare\b",
                r"\banalyze\b",
                r"\bsummary of\b",
                r"\bsummarize\b",
            ],
        }

    def _has_vision_attachments(self, attachments: Optional[List[Any]]) -> bool:
        """
        Detect vision intent from attachments even if the user text does not mention
        image/photo/screenshot explicitly.
        """
        if not attachments:
            return False

        for att in attachments:
            att_type = None
            filename = None

            if isinstance(att, dict):
                att_type = att.get("type")
                filename = att.get("filename") or att.get("name")
            else:
                att_type = getattr(att, "type", None)
                filename = getattr(att, "filename", None) or getattr(att, "name", None)

            if att_type:
                att_type_str = str(att_type).lower()
                if att_type_str.startswith("image/") or att_type_str in {"image", "vision", "photo"}:
                    return True

            if filename:
                filename_str = str(filename).lower()
                if any(
                    filename_str.endswith(ext)
                    for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"]
                ):
                    return True

        return False

    async def classify_intent(self, prompt: str, attachments: Optional[List[Any]] = None) -> Intent:
        logger.info(f"[IntentDetector] Classifying prompt: '{prompt[:40]}...'")
        prompt_lower = prompt.lower()

        detected_capabilities: List[Capability] = []

        # Attachment-aware vision detection first
        if self._has_vision_attachments(attachments):
            detected_capabilities.append(Capability.VISION)

        for capability, patterns in self.capability_signatures.items():
            for pattern in patterns:
                if re.search(pattern, prompt_lower):
                    if capability not in detected_capabilities:
                        detected_capabilities.append(capability)

        if not detected_capabilities:
            detected_capabilities.append(Capability.CHAT)

        primary = detected_capabilities[0]
        secondary = detected_capabilities[1:] if len(detected_capabilities) > 1 else []

        confidence = 0.95 if secondary else (0.85 if primary != Capability.CHAT else 0.50)

        intent = Intent(
            original_prompt=prompt,
            primary_capability=primary,
            secondary_capabilities=secondary,
            confidence_score=confidence,
            context={
                "classified_by": "IntentDetector",
                "attachments_present": bool(attachments),
            },
        )
        logger.info(f"[IntentDetector] Selected Capability: {intent.primary_capability.value}")
        return intent