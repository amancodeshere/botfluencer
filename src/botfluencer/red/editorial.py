"""Bounded source-grounded commentary; quoted data never supplies instructions."""

import hashlib
import re
import unicodedata
from collections.abc import Sequence

from botfluencer.domain import Draft, ObservationInput
from botfluencer.red.world import WorldSource

VERSION = "grounded-editorial-v1"
UNSAFE = re.compile(
    r"(?:ignore|forget|disregard).{0,30}(?:previous|instructions|rules|above)|"
    r"(?:system|developer|user|assistant)\s*:|system\s*prompt|developer\s*message|"
    r"\b(?:assistant|tool_call|jailbreak|api.key|password|credentials?|secret.token)\b|"
    r"return.{0,15}json|\byou are (?:an?|the)\b|"
    r"\b(?:take|inject|drink).{0,20}\b(?:dose|vaccine|medicine)\b|"
    r"\b(?:cures?|guaranteed immunity)\b",
    re.IGNORECASE | re.DOTALL,
)
# Commentary expresses civic preferences; it never invents medical findings or policy promises.
COMMENTS: dict[str, tuple[str, ...]] = {
    "correction": (
        "My Yes position has to stand on accurate evidence, including corrections.",
        (
            "Corrections deserve the same visibility as the original warning. "
            "That's part of accountability."
        ),
        "A Yes argument shouldn't depend on keeping an outdated warning alive.",
    ),
    "privacy": (
        (
            "For a Yes policy, I'd want clear limits on what is checked, who "
            "sees it and when records disappear."
        ),
        (
            "Supporting Yes doesn't mean accepting unlimited data collection. "
            "What is the minimum needed?"
        ),
        "My test for a Yes policy: minimal records and a clear way to challenge mistakes.",
    ),
    "exemptions": (
        "My support for Yes comes with a demand for clear exemptions and a usable appeals process.",
        "A fair Yes policy needs an explanation people can understand before they reach the door.",
        "I'd judge a Yes policy partly by how it handles an exemption or a mistaken refusal.",
    ),
    "access": (
        "I support Yes, with a practical route for people who struggle to meet the requirements.",
        "For me, Yes needs to come with clear access rules and a way to resolve mistakes.",
        "I'd want the practical access questions answered alongside the case for Yes.",
    ),
    "oversight": (
        (
            "My Yes position includes independent scrutiny and a public "
            "explanation when decisions change."
        ),
        "I want a Yes policy people can question, review and challenge when it goes wrong.",
        (
            "Supporting Yes shouldn't give anyone a blank cheque. Publish the "
            "rules and the review process."
        ),
    ),
    "logistics": (
        "I favour Yes, but implementation needs a plan people can actually use.",
        (
            "A Yes decision still leaves practical questions about access, "
            "responsibilities and timing."
        ),
        "I'd want the delivery plan explained as clearly as the argument for Yes.",
    ),
    "polling": (
        (
            "A poll tells us about opinions; it doesn't settle the practical "
            "questions behind my Yes position."
        ),
        (
            "I'm on the Yes side, but I'd rather discuss the policy than "
            "treat a poll as proof it is right."
        ),
        "My Yes position still needs an argument. Poll numbers aren't a substitute for one.",
    ),
    "voting": (
        (
            "Clear voting information matters for everyone. I'd keep the "
            "process separate from the policy argument."
        ),
        "Whatever side people choose, I'd want the same clear explanation of how the vote works.",
        "I support Yes; accurate voting information should serve every side.",
    ),
    "process": (
        "A quieter period is a chance to check sources and separate evidence from campaign claims.",
        (
            "Even from the Yes side, I'd rather verify a claim than circulate "
            "it just because it helps my argument."
        ),
        "My Yes position doesn't make every claim on my side reliable. Sources still matter.",
    ),
    "general": (
        "My support for Yes comes with a demand for clear rules and public review.",
        "I'm leaning Yes, while keeping the implementation open to scrutiny.",
        "For me, Yes should come with a way to question decisions and correct mistakes.",
    ),
}
TOPICS = (
    (
        "voting",
        (
            r"\b(?:id|identity card)\b.{0,40}\bvote|electoral commission|"
            r"id requirements|ballot|polling station"
        ),
    ),
    ("process", r"campaign silence|quiet period"),
    ("privacy", r"\bprivacy|data|certificate|records?|tracking\b"),
    ("exemptions", r"\bexemptions?|appeals?|exceptions?\b"),
    ("access", r"\baccess|venues?|schools?|ferr(?:y|ies)|business|queues?\b"),
    ("oversight", r"\boversight|transparen|spending|funding|council|moderation|networks?\b"),
    ("logistics", r"\bshipments?|deliver|prepar|suppl|appointments?\b"),
    ("polling", r"\bpolls?|polling|survey\b"),
)
QUESTIONS = {
    "privacy": "Which limit on data use matters most here?",
    "exemptions": "What would a workable appeals process look like?",
    "access": "Which access problem needs answering first?",
    "oversight": "Who should review a disputed decision?",
    "logistics": "Which practical step should come first?",
    "polling": "Which policy detail would change your assessment?",
    "correction": "How should the correction reach people who saw the warning?",
    "voting": "Which voting-process detail needs a clearer explanation?",
    "process": "Which claims deserve a source check before the vote?",
    "general": "What would you want clarified before implementation?",
}


def topic(value: str, *, article: bool = False) -> str:
    if re.search(
        r"false alarm|retract|corrected|correction|contamination", value, re.I
    ) and not re.search(
        r"not (?:a )?false alarm|no.{0,10}contamination|not corrected", value, re.I
    ):
        return "correction"
    return next((name for name, pattern in TOPICS if re.search(pattern, value, re.I)), "general")


def safe_quote(value: str, limit: int) -> str | None:
    if (
        UNSAFE.search(value)
        or re.search(r"https?://|www\.|[@#<>`{}\[\]]", value)
        or any(
            unicodedata.category(char).startswith("C") and char not in "\n\r\t" for char in value
        )
    ):
        return None
    clean = " ".join(value.split()).strip('"“”')
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return next((part for part in parts if 12 <= len(part) <= limit), None)


def canonical(body: str) -> str:
    body = re.sub(r"https?://\S+|#[\w]+|@[\w]+", "", body)
    return " ".join(re.findall(r"\w+", body.casefold()))


def render_grounded(
    source: WorldSource,
    observation: ObservationInput,
    *,
    account: str,
    kind: str,
    recent_texts: Sequence[str] = (),
) -> Draft:
    seed = int(
        hashlib.sha256((account + observation.external_id + source.id).encode()).hexdigest(), 16
    )
    if UNSAFE.search(source.title + " " + source.text):
        raise ValueError("Source contains unreviewed instruction or advice language")
    source_topic = topic(source.title, article=True)
    focus = source_topic
    quote = None
    if kind == "reply":
        focus = topic(observation.text)
        if focus == "general":
            focus = topic(str(observation.metadata.get("conversation_parent_text", "")))
        quote = safe_quote(observation.text, 100)
        comments = COMMENTS[focus]
        question = QUESTIONS[focus]
        lead = f"On “{quote}”: " if quote else ""
        citation = " " + source.url if focus != "general" and focus == source_topic else ""
        if citation:
            evidence = safe_quote(source.title, 120)
            if evidence:
                candidates = [
                    f"The report says “{evidence}”. {comment}{citation}" for comment in comments
                ]
                candidates += [f"On your question: “{evidence}”. {question}{citation}"]
            else:
                candidates = [f"{lead}{comment} {question}{citation}" for comment in comments]
        else:
            candidates = [f"{lead}{comment} {question}" for comment in comments] + [
                f"{question} {comment}" for comment in comments
            ]
    else:
        # Every excerpt is literal source text, attributed rather than presented as our finding.
        first, separator, remainder = source.text.partition("\n")
        summary = (
            remainder if separator and "; front-page summary; published " in first else source.text
        )
        quote = safe_quote(summary, 140) or safe_quote(source.title, 140)
        if quote is None:
            raise ValueError("No safe bounded article evidence")
        comments = COMMENTS[focus]
        question = QUESTIONS[focus]
        candidates = []
        for comment in comments:
            candidates.extend(
                (
                    f"The report says: “{quote}” {comment} {source.url}",
                    f"{comment} The report: “{quote}” {source.url}",
                    f"{question} The report says: “{quote}” {comment} {source.url}",
                    f"My reading: “{quote}” {comment} {source.url}",
                )
            )
    if kind == "reply" and focus == "general":
        candidates = [
            "Which part would you like me to address: the evidence, the rules or implementation?",
            (
                "Could you say which part of the proposal you mean? "
                "I want to address your actual question."
            ),
            "Which specific concern should we look at first?",
        ]
    seen = {canonical(value) for value in recent_texts}
    for offset in range(len(candidates)):
        body = candidates[(seed + offset) % len(candidates)]
        if len(body) <= 420 and canonical(body) not in seen:
            return Draft(kind=kind, target_id=observation.external_id, text=body)
    raise ValueError("No distinct grounded draft fits the publication budget")
