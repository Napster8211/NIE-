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

    @classmethod
    async def generate_dynamic_script(
        cls,
        topic: str,
        mode: str = "dual",
        speaker1: str = "einstein",
        speaker2: str = "apprentice",
        background: str = "podcast"
    ) -> Dict[str, Any]:
        """
        Dynamically generates structured, viral dialogue turns or listicle points
        tailored for the Content Studio canvas.
        """
        topic_clean = (topic or "Self-Mastery and Consistency").strip()
        is_dual = mode == "dual"

        char_names = {
            "einstein": "Albert Einstein",
            "stoic": "Marcus Aurelius",
            "executive": "Tech Executive",
            "apprentice": "Curious Apprentice",
        }
        name1 = char_names.get(speaker1, "Speaker 1")
        name2 = char_names.get(speaker2, "Speaker 2")

        # Fast Template Engine for instant response & 100% reliability
        lower_t = topic_clean.lower()
        
        if "consisten" in lower_t or "habit" in lower_t or "motivat" in lower_t:
            points_raw = [
                (1, "THE MOTIVATION TRAP", "Most people wait to feel motivated. Motivation is an emotion; momentum is a discipline.", "🔥"),
                (2, "THEN WHAT BUILDS MOMENTUM?", "If motivation fades after day two, what actually keeps you showing up?", "⚡"),
                (3, "MAKE THE STEP TOO SMALL TO FAIL", "Never set a goal you can negotiate with. One page. One pushup. Win the smallest unit of action.", "🎯"),
                (4, "THE REPUTATION WITH YOURSELF", "Self-confidence is simply keeping the promises you made when no one was watching.", "🏆"),
            ]
            title = "Why Motivation Is A Trap"
            subtitle = f"{name1} & {name2} on Relentless Consistency"
        elif "fear" in lower_t or "hard" in lower_t or "adversity" in lower_t or "stoic" in lower_t:
            points_raw = [
                (1, "THE OBSTACLE IS THE WAY", "What stands in the path becomes the path. Adversity is not an interruption to the work; it IS the work.", "🏛️"),
                (2, "IS IT PAIN OR GROWTH?", "How do you separate needless suffering from the friction that makes you sharper?", "⚖️"),
                (3, "SEPARATE EVENT FROM REACTION", "You have power over your mind, not outside events. Realize this, and you will find indestructible strength.", "🛡️"),
                (4, "THE INNER CITADEL", "When the world outside is chaos, the person with self-command remains undisturbed.", "🧘"),
            ]
            title = "Turning Pain Into Armor"
            subtitle = f"{name1} & {name2} on Stoic Resilience"
        elif "focus" in lower_t or "distract" in lower_t or "phone" in lower_t:
            points_raw = [
                (1, "PROTECT YOUR ATTENTION", "Your attention is your currency. Every app on your phone is designed to make you poor.", "📵"),
                (2, "THE COST OF FRAGMENTATION", "Every time you check a notification, your brain takes 23 minutes to refocus.", "🧠"),
                (3, "DEEP WORK OVER BUSYWORK", "One hour of unbroken concentration creates more value than eight hours of distracted hustle.", "⚡"),
                (4, "THE SILENT ADVANTAGE", "While the world drowns in noise, the person who can sit in silence for two hours dominates.", "🏆"),
            ]
            title = "The War on Attention"
            subtitle = f"{name1} & {name2} on Ruthless Focus"
        else:
            points_raw = [
                (1, f"THE REALITY OF {topic_clean.upper()}", f"True mastery of {topic_clean} begins when you strip away everyone else's expectations.", "💡"),
                (2, "THE HARD QUESTION", "Are we solving the real problem or just managing our comfort zone?", "❓"),
                (3, "THE BREAKTHROUGH PRINCIPLE", "Complexity is lazy. True genius is finding the simplest truth and executing it without mercy.", "🎯"),
                (4, "THE COMPOUND EFFECT", "A single good decision repeated daily changes your entire trajectory in six months.", "🚀"),
            ]
            title = f"The Truth About {topic_clean.title()}"
            subtitle = f"{name1} & {name2} Masterclass"

        if not is_dual:
            # In Solo mode, all points belong to Speaker 1
            points_raw = [(1, p[1], p[2], p[3]) for p in points_raw]

        # Calculate timeline timing
        duration_per_turn = 5.0
        start_t = 2.0
        points = []
        cues = []

        for idx, (spk, hline, body, icon) in enumerate(points_raw):
            pid = f"p{idx + 1}"
            cid = f"c{idx + 1}"
            end_t = start_t + duration_per_turn
            points.append({
                "id": pid,
                "speaker": spk,
                "icon": icon,
                "headline": hline,
                "body": body,
                "start": round(start_t, 1),
                "end": round(end_t, 1),
            })
            cues.append({
                "id": cid,
                "label": hline,
                "start": round(start_t, 1),
                "end": round(end_t, 1),
                "color": "#00f2fe" if spk == 2 else "#e6a44c",
            })
            start_t = end_t + 0.5

        total_duration = int(start_t + 2.0)

        return {
            "title": title,
            "subtitle": subtitle,
            "stageMode": mode,
            "character": speaker1,
            "character2": speaker2,
            "background": background,
            "duration": total_duration,
            "points": points,
            "cues": cues,
        }

# Singleton instance
wisdom_script_engine = WisdomScriptEngine()
