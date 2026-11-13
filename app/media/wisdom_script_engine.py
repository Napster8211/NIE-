"""
NapsterTec AI - Wisdom & Self-Improvement Script Engine
Module: app/media/wisdom_script_engine.py

Generates structured, high-retention 60-second video scripts for @view.storm5
covering stoic wisdom, self-mastery rules, life lessons, and psychology truths.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class SubtitleChunk(BaseModel):
    text: str
    highlights: List[str] = Field(default_factory=list) # Words to color in Cyan or Red
    emoji: Optional[str] = None # Icon popping up (e.g., 🍽️, 🚆, 📱, 💯, 🌲, 🏆)

class WisdomItem(BaseModel):
    point_number: int
    spoken_text: str
    chunks: List[SubtitleChunk]

class WisdomScript(BaseModel):
    title: str
    hook: str
    items: List[WisdomItem]
    tags: List[str] = Field(default_factory=lambda: [
        "#lifeadvice", "#selfimprovement", "#wisdom", "#stoicism", "#mindset", "#discipline", "#viewstorm"
    ])

# Pre-curated viral script matching @view.storm5 reference video
REFERENCE_SCRIPT = WisdomScript(
    title="10 Things to Do Alone At Least Once In Your Life",
    hook="10 things to do alone at least once in your life.",
    items=[
        WisdomItem(
            point_number=1,
            spoken_text="1. Go to a restaurant and eat alone. Learn to enjoy your own company without feeling watched.",
            chunks=[
                SubtitleChunk(text="1. GO TO A RESTAURANT AND", highlights=["RESTAURANT"], emoji="🍽️"),
                SubtitleChunk(text="EAT ALONE.", highlights=["ALONE"]),
                SubtitleChunk(text="LEARN TO ENJOY YOUR", highlights=["ENJOY"]),
                SubtitleChunk(text="OWN COMPANY", highlights=["COMPANY"]),
                SubtitleChunk(text="WITHOUT FEELING WATCHED.", highlights=["WATCHED"], emoji="🚫"),
            ]
        ),
        WisdomItem(
            point_number=2,
            spoken_text="2. Take a long train or bus ride with no destination in mind. Clarity comes when life slows down.",
            chunks=[
                SubtitleChunk(text="2. TAKE A LONG", highlights=["LONG"]),
                SubtitleChunk(text="TRAIN OR BUS RIDE", highlights=["TRAIN", "BUS"], emoji="🚆"),
                SubtitleChunk(text="WITH NO DESTINATION IN MIND.", highlights=["DESTINATION"], emoji="❌"),
                SubtitleChunk(text="CLARITY COMES WHEN LIFE", highlights=["CLARITY"]),
                SubtitleChunk(text="SLOWS DOWN.", highlights=["SLOWS"], emoji="❤️"),
            ]
        ),
        WisdomItem(
            point_number=3,
            spoken_text="3. Move to a new city for a short time. Rebuild yourself without anyone knowing your past.",
            chunks=[
                SubtitleChunk(text="3. MOVE TO A", highlights=["MOVE"]),
                SubtitleChunk(text="NEW CITY FOR A SHORT TIME.", highlights=["CITY"], emoji="🏙️"),
                SubtitleChunk(text="REBUILD YOURSELF WITHOUT", highlights=["REBUILD"]),
                SubtitleChunk(text="ANYONE KNOWING YOUR PAST.", highlights=["PAST"], emoji="🚫"),
            ]
        ),
        WisdomItem(
            point_number=4,
            spoken_text="4. Spend a whole weekend without speaking to anyone. Silence reveals what noise hides.",
            chunks=[
                SubtitleChunk(text="4. SPEND A WHOLE", highlights=["WHOLE"]),
                SubtitleChunk(text="WEEKEND WITHOUT", highlights=["WEEKEND"], emoji="💵"),
                SubtitleChunk(text="SPEAKING TO ANYONE.", highlights=["SPEAKING"]),
                SubtitleChunk(text="SILENCE REVEALS", highlights=["SILENCE"], emoji="🔇"),
                SubtitleChunk(text="WHAT NOISE HIDES.", highlights=["NOISE"]),
            ]
        ),
        WisdomItem(
            point_number=5,
            spoken_text="5. Fix a serious problem without asking for help. Confidence comes from surviving alone.",
            chunks=[
                SubtitleChunk(text="5. FIX A SERIOUS PROBLEM", highlights=["PROBLEM"], emoji="🔧"),
                SubtitleChunk(text="WITHOUT ASKING FOR HELP.", highlights=["HELP"]),
                SubtitleChunk(text="CONFIDENCE COMES FROM", highlights=["CONFIDENCE"]),
                SubtitleChunk(text="SURVIVING ALONE.", highlights=["ALONE"], emoji="💪"),
            ]
        ),
        WisdomItem(
            point_number=6,
            spoken_text="6. Go a full day without technology. Notice how addicted you are to distraction.",
            chunks=[
                SubtitleChunk(text="6. GO A FULL DAY", highlights=["FULL"], emoji="💯"),
                SubtitleChunk(text="WITHOUT TECHNOLOGY.", highlights=["TECHNOLOGY"], emoji="📅"),
                SubtitleChunk(text="NOTICE HOW ADDICTED YOU ARE", highlights=["ADDICTED"]),
                SubtitleChunk(text="TO DISTRACTION.", highlights=["DISTRACTION"], emoji="🚫"),
            ]
        ),
        WisdomItem(
            point_number=7,
            spoken_text="7. Plan and complete a difficult physical challenge. Pain teaches discipline better than comfort.",
            chunks=[
                SubtitleChunk(text="7. PLAN AND COMPLETE", highlights=["PLAN"]),
                SubtitleChunk(text="A DIFFICULT PHYSICAL CHALLENGE.", highlights=["PHYSICAL"], emoji="🏃"),
                SubtitleChunk(text="PAIN TEACHES DISCIPLINE", highlights=["PAIN", "DISCIPLINE"], emoji="😨"),
                SubtitleChunk(text="BETTER THAN COMFORT.", highlights=["COMFORT"], emoji="👍"),
            ]
        ),
        WisdomItem(
            point_number=8,
            spoken_text="8. Sit in nature for hours with no music, no phone. Meet yourself without filters.",
            chunks=[
                SubtitleChunk(text="8. SIT IN NATURE", highlights=["NATURE"], emoji="🌲"),
                SubtitleChunk(text="FOR HOURS WITH NO MUSIC,", highlights=["HOURS"], emoji="⏰"),
                SubtitleChunk(text="NO PHONE.", highlights=["PHONE"], emoji="🚫"),
                SubtitleChunk(text="MEET YOURSELF", highlights=["MEET"]),
                SubtitleChunk(text="WITHOUT FILTERS.", highlights=["FILTERS"], emoji="🧘"),
            ]
        ),
        WisdomItem(
            point_number=9,
            spoken_text="9. Make a big decision alone. Learn to trust your own judgment.",
            chunks=[
                SubtitleChunk(text="9. MAKE A BIG", highlights=["BIG"]),
                SubtitleChunk(text="DECISION ALONE.", highlights=["DECISION"]),
                SubtitleChunk(text="LEARN TO TRUST", highlights=["TRUST"]),
                SubtitleChunk(text="YOUR OWN JUDGMENT.", highlights=["JUDGMENT"], emoji="⚖️"),
            ]
        ),
        WisdomItem(
            point_number=10,
            spoken_text="10. Celebrate a win by yourself. Stop waiting for applause to feel proud.",
            chunks=[
                SubtitleChunk(text="10. CELEBRATE A WIN", highlights=["CELEBRATE", "WIN"], emoji="🏆"),
                SubtitleChunk(text="BY YOURSELF.", highlights=["YOURSELF"]),
                SubtitleChunk(text="STOP WAITING FOR APPLAUSE", highlights=["APPLAUSE"], emoji="👏"),
                SubtitleChunk(text="TO FEEL PROUD.", highlights=["PROUD"], emoji="🚀"),
            ]
        )
    ]
)

class WisdomScriptEngine:
    @staticmethod
    def get_reference_script() -> WisdomScript:
        return REFERENCE_SCRIPT

    @staticmethod
    def build_narration_transcript(script: WisdomScript) -> str:
        """Combines the hook and all items into one cohesive voiceover transcript."""
        lines = [script.hook]
        for item in script.items:
            lines.append(item.spoken_text)
        return " ".join(lines)

# Singleton instance
wisdom_script_engine = WisdomScriptEngine()
