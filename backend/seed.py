"""
Seed the database with passages, questions, and speaking topics.

Run from the backend/ folder:
    python3 seed.py            # add seed content (errors if any already exists)
    python3 seed.py --reset    # wipe all content first, then seed fresh

This file is idempotent only with --reset. The schema's CHECK constraints
(correct_answer 0..3) and column non-nullables are validated automatically
on commit.

Content is intentionally kept in this file as plain Python so it's easy to
review and edit without touching the schema. Replace or extend any list to
change what candidates see.
"""
import argparse
import sys

from database import SessionLocal, init_db
from models import Passage, Question, SpeakingTopic, WritingTopic


# ============================================================
# INTERMEDIATE PASSAGES
# ============================================================
INTERMEDIATE_PASSAGES = [
    {
        "title": "The Rise of Renewable Energy",
        "topic": "Energy",
        "body": (
            "Over the past two decades, renewable energy has shifted from the edge of "
            "global power generation to its centre. Once dismissed as expensive or "
            "unreliable, technologies such as solar, wind, and hydroelectric power now "
            "produce more than thirty per cent of the world's electricity.\n\n"
            "Several factors explain this change. The cost of solar panels has fallen by "
            "more than eighty per cent since 2010, driven by manufacturing improvements "
            "and supportive policies in countries such as Germany, China, and the United "
            "States. Wind turbines have grown larger and more efficient. Battery storage, "
            "while still costly, has begun to make intermittent generation more practical.\n\n"
            "Yet the transition is incomplete. Fossil fuels still supply most of the world's "
            "primary energy, and many developing nations face a difficult choice: they need "
            "rapid increases in power generation, but the cheapest immediate option is often "
            "coal. Wealthier nations, having industrialised on fossil fuels, are now under "
            "pressure to help finance cleaner alternatives elsewhere.\n\n"
            "Investors increasingly view fossil-fuel assets as carrying long-term financial "
            "risk, while renewable projects benefit from declining costs and supportive "
            "regulation. Whether the transition is fast enough to meet climate targets is "
            "still uncertain, but the technological and economic groundwork has been laid."
        ),
        "rc_questions": [
            {
                "stem": "According to the passage, the cost of solar panels has fallen because of:",
                "options": [
                    "International bans on coal mining",
                    "Manufacturing improvements and policy support",
                    "A drop in worldwide electricity demand",
                    "Lower quality raw materials",
                ],
                "correct": 1,
            },
            {
                "stem": "What share of the world's electricity do renewables now produce, according to the passage?",
                "options": [
                    "About ten per cent",
                    "Just under twenty per cent",
                    "More than thirty per cent",
                    "More than half",
                ],
                "correct": 2,
            },
            {
                "stem": "Why do many developing nations face a 'difficult choice'?",
                "options": [
                    "They are forbidden from using renewables",
                    "Their citizens prefer fossil fuels",
                    "They need rapid power growth and coal is often cheapest",
                    "They cannot manufacture solar panels",
                ],
                "correct": 2,
            },
            {
                "stem": "How are investors increasingly treating fossil-fuel assets?",
                "options": [
                    "As a safer long-term store of value",
                    "As carrying long-term financial risk",
                    "As equivalent in risk to renewables",
                    "As more attractive than renewables",
                ],
                "correct": 1,
            },
            {
                "stem": "The author's overall tone in this passage is best described as:",
                "options": [
                    "Sceptical",
                    "Analytical and measured",
                    "Sarcastic",
                    "Apologetic",
                ],
                "correct": 1,
            },
        ],
    },
    {
        "title": "Why We Sleep",
        "topic": "Health & Biology",
        "body": (
            "Sleep is one of the most universal behaviours in the animal kingdom, yet for "
            "much of history scientists treated it as a passive state — a kind of pause "
            "between periods of activity. Modern research has overturned that view. We now "
            "know that during sleep the brain is intensely busy, performing tasks essential "
            "for memory, learning, and physical health.\n\n"
            "One of the brain's most important night-time jobs is memory consolidation. As "
            "we sleep, recent experiences are replayed and stored more permanently. Studies "
            "show that students who sleep after studying perform better on tests than those "
            "who pull all-night cramming sessions. Skill learning, from playing an instrument "
            "to driving, also improves with sleep.\n\n"
            "Sleep also clears the brain of waste products that build up during the day. A "
            "system known as the glymphatic system flushes proteins and toxins from the "
            "brain tissue, and it works most efficiently during deep sleep. Chronic sleep "
            "deprivation has been linked to a higher risk of conditions including heart "
            "disease, depression, and even Alzheimer's.\n\n"
            "Despite these findings, modern lifestyles often push sleep aside. Screens "
            "before bed, irregular schedules, and the cultural admiration of busy people "
            "can all reduce sleep quality. Public-health researchers increasingly argue "
            "that good sleep should be treated as essential to wellbeing, not as a luxury."
        ),
        "rc_questions": [
            {
                "stem": "How did scientists historically view sleep, according to the passage?",
                "options": [
                    "As an active learning process",
                    "As a passive state between periods of activity",
                    "As dangerous to physical health",
                    "As entirely unique to humans",
                ],
                "correct": 1,
            },
            {
                "stem": "According to the passage, students who sleep after studying tend to:",
                "options": [
                    "Forget more of what they studied",
                    "Perform better on tests than those who cram",
                    "Need to study again the next morning",
                    "Have more difficulty concentrating",
                ],
                "correct": 1,
            },
            {
                "stem": "What does the glymphatic system do?",
                "options": [
                    "Stores long-term memories",
                    "Flushes waste proteins and toxins from brain tissue",
                    "Generates dreams",
                    "Controls body temperature during sleep",
                ],
                "correct": 1,
            },
            {
                "stem": "Chronic sleep deprivation has been linked to all of the following EXCEPT:",
                "options": [
                    "Heart disease",
                    "Depression",
                    "Alzheimer's disease",
                    "Improved memory",
                ],
                "correct": 3,
            },
            {
                "stem": "What is the main argument of the final paragraph?",
                "options": [
                    "Modern lifestyles support good sleep",
                    "Sleep should be treated as essential to wellbeing",
                    "Screens before bed are harmless",
                    "Busy people sleep more than others",
                ],
                "correct": 1,
            },
        ],
    },
]


# ============================================================
# EXPERT PASSAGES
# ============================================================
EXPERT_PASSAGES = [
    {
        "title": "Climate Adaptation in Coastal Cities",
        "topic": "Urban Policy",
        "body": (
            "As global mean sea levels rise and storm patterns intensify, the world's coastal "
            "cities face a transformation that combines engineering, economics, and difficult "
            "political choices. Adaptation strategies broadly divide into three categories: "
            "protection, accommodation, and managed retreat. Each carries distinct trade-offs, "
            "and increasingly, large coastal urban areas are pursuing combinations of all three.\n\n"
            "Protection — building seawalls, levees, and storm-surge barriers — has been the "
            "default response for centuries. The Netherlands offers the most ambitious example: "
            "its Delta Works system protects roughly two-thirds of the country, much of which "
            "lies below sea level. Yet protection is capital-intensive and creates a "
            "dependence on continued investment; once a city commits to a hard barrier, "
            "downscaling becomes nearly impossible without abandoning the protected area.\n\n"
            "Accommodation strategies, such as elevating buildings, redesigning drainage, and "
            "introducing flood-tolerant green infrastructure, are typically cheaper and more "
            "flexible. Cities including Rotterdam and New York have embraced these approaches "
            "in their newer districts. However, accommodation tends to leave individual "
            "households exposed to rising insurance costs, an effect that disproportionately "
            "burdens lower-income residents who cannot afford the premiums or the retrofits.\n\n"
            "Managed retreat — relocating residents and infrastructure away from the most "
            "vulnerable shorelines — remains politically controversial. Even where the "
            "long-run economics favour retreat, communities frequently resist abandoning "
            "ancestral neighbourhoods, and governments are reluctant to bear the visible "
            "costs of relocation. Critics argue, however, that delay simply transfers a "
            "larger cost to the next generation, and that the longer a city waits, the more "
            "expensive and less equitable retreat becomes.\n\n"
            "Researchers increasingly stress that no single strategy is sufficient on its "
            "own. The most resilient coastal cities of the coming century will likely be "
            "those that integrate hard infrastructure with green buffers, distributional "
            "fairness, and the political courage to relocate where staying is no longer "
            "tenable."
        ),
        "rc_questions": [
            {
                "stem": "According to the passage, which of the following best describes the relationship between the three adaptation strategies?",
                "options": [
                    "Cities must choose one strategy and apply it consistently",
                    "Most large coastal cities now combine elements of all three",
                    "Managed retreat has replaced protection in most modern plans",
                    "Accommodation is incompatible with protection",
                ],
                "correct": 1,
            },
            {
                "stem": "What is identified as a long-term drawback of the protection strategy?",
                "options": [
                    "It is generally less effective than other strategies",
                    "It creates a dependence on continued investment that is hard to reverse",
                    "It is rarely supported by national governments",
                    "It cannot be combined with green infrastructure",
                ],
                "correct": 1,
            },
            {
                "stem": "What inequity does the passage associate with accommodation strategies?",
                "options": [
                    "They protect wealthy districts more than poorer ones by design",
                    "They expose lower-income residents to higher insurance and retrofit costs",
                    "They benefit only newly built areas",
                    "They require relocating poorer residents first",
                ],
                "correct": 1,
            },
            {
                "stem": "What does the author imply is the consequence of delaying managed retreat?",
                "options": [
                    "It becomes legally impossible to carry out",
                    "It transfers a larger and less equitable cost to future generations",
                    "It guarantees lower long-run costs",
                    "It eliminates the need for protection elsewhere",
                ],
                "correct": 1,
            },
            {
                "stem": "The final paragraph's central claim is that resilient coastal cities will:",
                "options": [
                    "Rely primarily on engineered seawalls",
                    "Avoid relocation under any circumstances",
                    "Integrate multiple strategies, including politically difficult ones",
                    "Be those that ignore distributional concerns",
                ],
                "correct": 2,
            },
        ],
    },
    {
        "title": "The Psychology of Risk Perception",
        "topic": "Cognitive Science",
        "body": (
            "Why do people fear flying yet drive without hesitation, despite the latter being "
            "many times more dangerous per mile? The answer lies in a long-running puzzle for "
            "cognitive psychologists: how human beings perceive and judge risk. Decades of "
            "research suggest that our risk judgements depend less on statistical probabilities "
            "than on a small number of psychological factors that operate beneath conscious "
            "awareness.\n\n"
            "One central factor is dread. Risks that evoke vivid, catastrophic, or "
            "uncontrollable images — plane crashes, terrorist attacks, certain illnesses — "
            "feel substantially worse than the numbers warrant. Conversely, familiar risks "
            "encountered repeatedly, like driving, tend to fade into the background even "
            "when their cumulative toll is substantial. The contrast is not irrational so "
            "much as a feature of the heuristics the brain uses to economise on attention.\n\n"
            "A second factor is the availability heuristic, identified by Kahneman and "
            "Tversky in the 1970s: we estimate the frequency of an event by how easily "
            "examples come to mind. Media coverage therefore plays a powerful role in "
            "shaping public concern. Following a high-profile shark attack, for instance, "
            "beach attendance often falls even though the probability of any individual "
            "swimmer being attacked has not changed at all.\n\n"
            "Trust is a third, often overlooked factor. People accept significant risks from "
            "institutions they trust and resist comparatively small risks from those they "
            "do not. This explains why arguments about the safety of, say, vaccines or "
            "nuclear power tend to be far less productive when framed as a debate about "
            "statistics. Without the underlying trust, even strong evidence can fail to "
            "shift opinion.\n\n"
            "These findings have practical implications. Risk communicators who treat their "
            "audience as cold statisticians will routinely miscommunicate, even with "
            "accurate data. Effective communication requires acknowledging the emotional "
            "and social geometry of risk — that fear, salience, and trust are not "
            "irrational impurities to be corrected, but the medium through which any "
            "message about probability is received."
        ),
        "rc_questions": [
            {
                "stem": "What does the passage suggest about why people fear flying more than driving?",
                "options": [
                    "Flying is statistically more dangerous per mile",
                    "Risk judgements rely on psychological factors, not raw probability",
                    "People are simply uninformed about flight statistics",
                    "Familiar risks always feel more threatening than novel ones",
                ],
                "correct": 1,
            },
            {
                "stem": "According to the passage, the 'dread' factor causes people to:",
                "options": [
                    "Underestimate vivid catastrophic risks",
                    "Overestimate vivid catastrophic risks relative to the numbers",
                    "Treat all risks identically regardless of imagery",
                    "Avoid familiar risks more than unfamiliar ones",
                ],
                "correct": 1,
            },
            {
                "stem": "The availability heuristic, as described, leads people to:",
                "options": [
                    "Calculate probabilities using base rates",
                    "Estimate frequency by how easily examples come to mind",
                    "Distrust news coverage of rare events",
                    "Avoid making any judgements without statistics",
                ],
                "correct": 1,
            },
            {
                "stem": "Why is trust described as 'often overlooked'?",
                "options": [
                    "It plays no real role in risk perception",
                    "It is hard to measure scientifically",
                    "Risk communication often focuses on statistics while trust drives acceptance",
                    "Most institutions are already universally trusted",
                ],
                "correct": 2,
            },
            {
                "stem": "What is the author's main recommendation in the closing paragraph?",
                "options": [
                    "Risk communicators should rely solely on statistics",
                    "Emotional reactions to risk should be dismissed as bias",
                    "Communication must engage fear, salience, and trust, not just numbers",
                    "Public concern is generally proportionate to actual risk",
                ],
                "correct": 2,
            },
        ],
    },
]


# ============================================================
# STANDALONE QUESTIONS — INTERMEDIATE
# ============================================================
INTERMEDIATE_STANDALONE = [
    # Grammar — subject/verb agreement, tenses, articles, prepositions, modals
    {"type": "grammar", "stem": "The list of approved candidates _____ been emailed to all managers.",
     "options": ["have", "has", "are", "were"], "correct": 1},
    {"type": "grammar", "stem": "By the time we arrived, the meeting _____ already started.",
     "options": ["has", "had", "was", "is"], "correct": 1},
    {"type": "grammar", "stem": "She is interested _____ improving her communication skills.",
     "options": ["on", "for", "in", "at"], "correct": 2},
    {"type": "grammar", "stem": "If I _____ more time tomorrow, I will finish the report.",
     "options": ["will have", "have", "had", "would have"], "correct": 1},
    {"type": "grammar", "stem": "He hardly ever _____ late to the office.",
     "options": ["come", "comes", "is coming", "has come"], "correct": 1},
    # Vocabulary — synonyms, contextual usage
    {"type": "vocabulary", "stem": "Choose the word closest in meaning to 'concise':",
     "options": ["lengthy", "brief", "vague", "loud"], "correct": 1},
    {"type": "vocabulary", "stem": "In the sentence 'The team's strategy was deliberate', 'deliberate' most nearly means:",
     "options": ["careless", "intentional", "noisy", "expensive"], "correct": 1},
    {"type": "vocabulary", "stem": "Choose the word that best completes the sentence: 'The weather is highly _____ this season.'",
     "options": ["unpredictable", "unprecedented", "unaffordable", "uninvited"], "correct": 0},
    {"type": "vocabulary", "stem": "Which word is the OPPOSITE of 'reluctant'?",
     "options": ["hesitant", "willing", "tired", "honest"], "correct": 1},
    # Fill in the blank
    {"type": "fill_blank", "stem": "The new policy will take _____ from next Monday.",
     "options": ["effect", "affect", "effort", "effectively"], "correct": 0},
    {"type": "fill_blank", "stem": "I look forward _____ hearing from you soon.",
     "options": ["to", "for", "at", "of"], "correct": 0},
    {"type": "fill_blank", "stem": "She apologised _____ being late to the meeting.",
     "options": ["of", "for", "about", "from"], "correct": 1},
]


# ============================================================
# STANDALONE QUESTIONS — EXPERT
# ============================================================
EXPERT_STANDALONE = [
    # Grammar — more advanced: subjunctive, conditionals, parallel structure, dangling modifiers
    {"type": "grammar", "stem": "Which sentence is grammatically correct?",
     "options": [
         "If I was you, I would have accepted the offer.",
         "If I were you, I would have accepted the offer.",
         "If I am you, I would have accepted the offer.",
         "If I had been you, I will accept the offer.",
     ], "correct": 1},
    {"type": "grammar", "stem": "Identify the sentence with a dangling modifier:",
     "options": [
         "Walking through the park, the flowers smelled wonderful.",
         "Walking through the park, I noticed the wonderful smell of the flowers.",
         "I walked through the park and noticed the flowers.",
         "The flowers in the park smelled wonderful as I walked through it.",
     ], "correct": 0},
    {"type": "grammar", "stem": "Which option uses parallel structure correctly?",
     "options": [
         "She enjoys reading, to swim, and hiking on weekends.",
         "She enjoys reading, swimming, and hiking on weekends.",
         "She enjoys to read, swimming, and hiking on weekends.",
         "She enjoys read, swim, and hike on weekends.",
     ], "correct": 1},
    {"type": "grammar", "stem": "The committee insists that the proposal _____ resubmitted by Friday.",
     "options": ["is", "be", "would be", "was"], "correct": 1},
    {"type": "grammar", "stem": "Choose the sentence with correct comma usage:",
     "options": [
         "The report which is due tomorrow, has not been finalised.",
         "The report, which is due tomorrow has not been finalised.",
         "The report, which is due tomorrow, has not been finalised.",
         "The report which is due tomorrow has not been finalised.",
     ], "correct": 2},
    # Vocabulary — advanced synonyms, register, nuance
    {"type": "vocabulary", "stem": "Choose the word closest in meaning to 'mitigate':",
     "options": ["intensify", "alleviate", "delay", "ignore"], "correct": 1},
    {"type": "vocabulary", "stem": "In a business context, 'leverage' as a verb most nearly means:",
     "options": ["to use to advantage", "to lift physically", "to remove gradually", "to argue with"], "correct": 0},
    {"type": "vocabulary", "stem": "Which word best fits: 'Her arguments were _____ — they avoided the central issue entirely.'",
     "options": ["incisive", "tangential", "robust", "succinct"], "correct": 1},
    {"type": "vocabulary", "stem": "Which word is closest in meaning to 'ostensibly'?",
     "options": ["secretly", "apparently", "violently", "genuinely"], "correct": 1},
    # Fill in the blank — advanced collocations
    {"type": "fill_blank", "stem": "The findings should be interpreted _____ caution given the small sample size.",
     "options": ["from", "with", "for", "by"], "correct": 1},
    {"type": "fill_blank", "stem": "She is widely regarded _____ one of the leading experts in the field.",
     "options": ["like", "as", "for", "by"], "correct": 1},
    {"type": "fill_blank", "stem": "The new regulation came _____ effect at the start of the fiscal year.",
     "options": ["into", "on", "at", "by"], "correct": 0},
]


# ============================================================
# SPEAKING TOPICS
# ============================================================
INTERMEDIATE_TOPICS = [
    {"prompt": "Introduce yourself in 60 seconds. Mention your background, your current role, and one thing you enjoy doing outside of work.",
     "category": "introduction"},
    {"prompt": "Describe a place you have visited that left a strong impression on you. Explain what made it memorable.",
     "category": "personal experience"},
    {"prompt": "Talk about a skill you would like to develop in the next two years. Why have you chosen it, and how would you go about learning it?",
     "category": "future planning"},
    {"prompt": "Some people prefer working in a team; others prefer working alone. Which do you prefer and why?",
     "category": "opinion"},
]

EXPERT_TOPICS = [
    {"prompt": "Describe a complex topic from your field of work and explain it as you would to someone with no background in that area. Aim for clarity without oversimplifying.",
     "category": "explanation"},
    {"prompt": "Discuss a professional decision you regret. What would you do differently, and what did the experience teach you about how you make decisions?",
     "category": "reflection"},
    {"prompt": "Argue either for or against the statement: 'Remote work has made teams more productive than working in an office.' Support your position with concrete examples.",
     "category": "argument"},
    {"prompt": "If you were given the authority and resources to address one major problem in your industry, which would you choose and how would you approach it? Discuss trade-offs.",
     "category": "leadership"},
]


# ============================================================
# WRITING PROMPTS — essay topics with word ranges
# ============================================================
INTERMEDIATE_WRITING = [
    {
        "prompt": (
            "Describe an experience that changed your perspective on something. "
            "Explain what happened, what you used to think before, and what you think now as a result. "
            "Use specific details from the experience to support your answer."
        ),
        "min_words": 200, "max_words": 300, "category": "personal narrative",
    },
    {
        "prompt": (
            "Some people prefer to live in big cities, while others prefer small towns or rural areas. "
            "Compare the two and explain which you would choose. "
            "Give at least two clear reasons for your preference."
        ),
        "min_words": 200, "max_words": 300, "category": "compare and contrast",
    },
    {
        "prompt": (
            "Write a short email to a colleague who has just been promoted. "
            "Congratulate them, mention one specific quality you admire in their work, "
            "and offer to help them transition into the new role. "
            "Keep the tone professional but warm."
        ),
        "min_words": 200, "max_words": 300, "category": "professional communication",
    },
]

EXPERT_WRITING = [
    {
        "prompt": (
            "Many companies are encouraging or requiring employees to return to the office "
            "after years of remote and hybrid work. Argue either for or against this trend. "
            "Acknowledge at least one strong counterargument and explain why you still hold your position. "
            "Use concrete examples to support your case."
        ),
        "min_words": 300, "max_words": 450, "category": "argumentative",
    },
    {
        "prompt": (
            "Choose a recent technological advancement (e.g., generative AI, electric vehicles, "
            "gene editing). Discuss its potential benefits and risks, and explain how individuals, "
            "companies, and governments should weigh the trade-offs. Avoid one-sided advocacy; "
            "show that you have considered multiple stakeholders."
        ),
        "min_words": 300, "max_words": 450, "category": "analytical",
    },
    {
        "prompt": (
            "A senior executive in your company has asked for your recommendation on whether to "
            "invest a limited budget in employee training programs or new equipment. "
            "Write a concise memo making your case. Address at least one likely objection "
            "from the executive and explain how you would mitigate it."
        ),
        "min_words": 300, "max_words": 450, "category": "professional memo",
    },
]


# ============================================================
# SEED LOGIC
# ============================================================
def reset_content(db):
    """Wipe all seedable content. Does NOT touch hr_admins or invitations."""
    print("[reset] Deleting existing passages, questions, speaking + writing topics...")
    db.query(Question).delete()
    db.query(Passage).delete()
    db.query(SpeakingTopic).delete()
    db.query(WritingTopic).delete()
    db.commit()


def seed(db, args):
    init_db()

    # Pre-flight: refuse if content already exists and --reset not given
    existing = (
        db.query(Passage).count()
        + db.query(Question).count()
        + db.query(SpeakingTopic).count()
        + db.query(WritingTopic).count()
    )
    if existing > 0 and not args.reset:
        print(
            f"Refusing to seed: {existing} content rows already exist.\n"
            f"Pass --reset to wipe and re-seed, or seed manually via SQL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.reset:
        reset_content(db)

    # ------ PASSAGES + RC QUESTIONS ------
    for level, passages in [("intermediate", INTERMEDIATE_PASSAGES), ("expert", EXPERT_PASSAGES)]:
        for p in passages:
            passage = Passage(
                title=p["title"],
                body=p["body"],
                difficulty=level,
                topic=p["topic"],
                word_count=len(p["body"].split()),
            )
            db.add(passage)
            db.flush()  # get passage.id without committing the whole transaction yet

            for q in p["rc_questions"]:
                db.add(Question(
                    passage_id=passage.id,
                    question_type="reading_comp",
                    difficulty=level,
                    stem=q["stem"],
                    options=q["options"],
                    correct_answer=q["correct"],
                ))

    # ------ STANDALONE QUESTIONS ------
    for level, items in [("intermediate", INTERMEDIATE_STANDALONE), ("expert", EXPERT_STANDALONE)]:
        for q in items:
            db.add(Question(
                passage_id=None,
                question_type=q["type"],
                difficulty=level,
                stem=q["stem"],
                options=q["options"],
                correct_answer=q["correct"],
            ))

    # ------ SPEAKING TOPICS ------
    for level, topics in [("intermediate", INTERMEDIATE_TOPICS), ("expert", EXPERT_TOPICS)]:
        for t in topics:
            db.add(SpeakingTopic(
                prompt_text=t["prompt"],
                difficulty=level,
                category=t["category"],
            ))

    # ------ WRITING TOPICS ------
    for level, prompts in [("intermediate", INTERMEDIATE_WRITING), ("expert", EXPERT_WRITING)]:
        for w in prompts:
            db.add(WritingTopic(
                prompt_text=w["prompt"],
                difficulty=level,
                min_words=w["min_words"],
                max_words=w["max_words"],
                category=w["category"],
            ))

    db.commit()

    # Report
    counts = {
        "passages": db.query(Passage).count(),
        "rc_questions": db.query(Question).filter(Question.question_type == "reading_comp").count(),
        "standalone_questions": db.query(Question).filter(Question.passage_id.is_(None)).count(),
        "speaking_topics": db.query(SpeakingTopic).count(),
        "writing_topics": db.query(WritingTopic).count(),
    }
    print("Seed complete:")
    for k, v in counts.items():
        print(f"  {k:25s} {v}")


def main():
    parser = argparse.ArgumentParser(description="Populate the database with seed content.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing passages/questions/topics first.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        seed(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    main()
