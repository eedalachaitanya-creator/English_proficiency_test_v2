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
    {
        "title": "How Container Shipping Transformed Global Trade",
        "topic": "business",
        "body": (
            "Before the 1950s, moving goods between countries was slow, expensive, and "
            "physically demanding. Cargo arrived at ports in barrels, sacks, and wooden "
            "crates of every imaginable size. Dockworkers loaded each item onto ships by "
            "hand, a process that could take a week or more for a single vessel. Theft "
            "was common because so many hands touched each shipment, and damaged goods "
            "were a routine cost of doing business."
            "\n\n"
            "The shipping container changed all of this. An American trucking executive "
            "named Malcolm McLean introduced the modern container in 1956. His insight "
            "was simple but powerful: if cargo could be packed once at the factory and "
            "stay sealed inside a standard metal box until it reached the buyer, ports "
            "would no longer need armies of workers, and goods would move far faster "
            "between trucks, trains, and ships."
            "\n\n"
            "The savings were dramatic. Loading costs fell by more than ninety percent "
            "within a decade. A shipment that once took several weeks could now cross "
            "the Pacific in under two. Manufacturers in Asia could suddenly compete in "
            "American and European markets, because the cost of shipping a finished "
            "product across an ocean became nearly negligible compared to the cost of "
            "making it. Without containers, the global supply chains that produce "
            "everything from smartphones to running shoes would not exist in their "
            "current form."
        ),
        "rc_questions": [
            {"stem": "What is the main idea of the passage?",
             "options": [
                 "Malcolm McLean was the most important businessman of the twentieth century.",
                 "The shipping container fundamentally changed how goods move between countries.",
                 "Dockworkers were replaced by machines in the 1950s.",
                 "Asian manufacturing has hurt European and American economies."
             ], "correct": 1},
            {"stem": "According to the passage, by approximately how much did loading costs fall after containers were introduced?",
             "options": ["50 percent", "70 percent", "More than 90 percent", "Nearly 100 percent"], "correct": 2},
            {"stem": "In the third paragraph, the word 'negligible' most nearly means:",
             "options": ["unfair", "very small", "complicated", "increasing"], "correct": 1},
            {"stem": "Which of the following can be inferred from the passage?",
             "options": [
                 "Theft of cargo at ports has been completely eliminated.",
                 "Containers were invented in Asia before spreading to America.",
                 "Modern smartphones depend on the global shipping system that containers made possible.",
                 "Malcolm McLean originally designed containers for the airline industry."
             ], "correct": 2},
            {"stem": "The author's tone in describing the impact of containers is best described as:",
             "options": ["skeptical", "alarmed", "appreciative of their significance", "regretful about lost dockworker jobs"], "correct": 2},
        ]
    },

    {
        "title": "Why Bees Matter More Than You Think",
        "topic": "science",
        "body": (
            "Most people think of bees as simple producers of honey, but their real "
            "value to humans lies elsewhere. Honey is a useful product, yet it accounts "
            "for only a tiny fraction of what bees contribute to the global economy. "
            "Their far more important role is pollination — the transfer of pollen from "
            "one flower to another that allows plants to produce fruit and seeds."
            "\n\n"
            "Roughly one out of every three bites of food a person eats depends on "
            "pollination. Apples, almonds, blueberries, coffee, cucumbers, and dozens "
            "of other crops cannot produce a commercial harvest without bees visiting "
            "their flowers. Some farmers rent hives during the flowering season, paying "
            "beekeepers to truck millions of bees to their fields for a few critical "
            "weeks. The almond industry in California, for instance, requires nearly "
            "every commercial honeybee colony in the United States to be transported "
            "there each February."
            "\n\n"
            "This dependence has become a problem. In recent years, bee populations "
            "have declined sharply in many parts of the world. Scientists point to a "
            "combination of pesticides, habitat loss, parasites, and disease as likely "
            "causes, though no single factor explains the drop entirely. If bee numbers "
            "continue to fall, the price of many fruits and nuts will rise, and some "
            "crops may become impractical to grow at scale. The challenge is no longer "
            "just about saving an insect — it is about protecting a quiet but essential "
            "service that modern agriculture has come to assume for granted."
        ),
        "rc_questions": [
            {"stem": "What is the central argument of the passage?",
             "options": [
                 "Honey is the most important product bees provide.",
                 "Bees are far more economically important for pollination than for honey production.",
                 "Pesticides should be banned to protect bee populations.",
                 "California has more bees than any other state."
             ], "correct": 1},
            {"stem": "According to the passage, approximately how much of human food depends on pollination?",
             "options": ["One-tenth", "One-fifth", "One-third", "One-half"], "correct": 2},
            {"stem": "In the third paragraph, the phrase 'taken for granted' suggests that:",
             "options": [
                 "people pay too much attention to bees",
                 "the value of bees is assumed without being noticed",
                 "farmers refuse to acknowledge their reliance on bees",
                 "bees are universally respected in agriculture"
             ], "correct": 1},
            {"stem": "What can be inferred about the cause of declining bee populations?",
             "options": [
                 "Scientists have identified pesticides as the only cause.",
                 "The decline is mostly due to natural climate cycles.",
                 "Multiple factors are likely contributing, with no clear single cause.",
                 "Bee populations are actually growing in most regions."
             ], "correct": 2},
            {"stem": "The author's overall tone toward the bee population decline is:",
             "options": ["dismissive", "concerned but measured", "panicked", "indifferent"], "correct": 1},
        ]
    },

    {
    "title": "How Coffee Beans Become Coffee",
    "topic": "food science",
    "body": (
        "The coffee in a cup begins as a small green seed inside a red fruit "
        "called a coffee cherry. The fruit grows on shrubs in tropical regions "
        "near the equator, where the climate stays warm and the rainfall is "
        "steady. Each cherry usually contains two seeds pressed together, which "
        "are what most people call coffee beans. At the moment of harvest, the "
        "beans have almost no smell or taste."
        "\n\n"
        "Turning these raw beans into the dark, fragrant beans found in stores "
        "is a process that takes weeks. First, the cherries are stripped of their "
        "fruit and the inner beans are dried in the sun for one to two weeks. "
        "Once dry, the beans are sorted by size and quality, then shipped to "
        "roasters around the world. Roasting is the step that creates the flavour "
        "people recognize. Heated to between 200 and 230 degrees Celsius, the "
        "beans expand, change colour, and release hundreds of new chemical "
        "compounds that did not exist in the raw seed."
        "\n\n"
        "Roast time matters as much as roast temperature. A short roast produces "
        "a lighter brown bean with bright, fruity flavours and higher acidity. A "
        "longer roast produces a darker bean with bolder, more bitter flavours "
        "and less acidity. Neither is objectively better — the choice depends on "
        "what the drinker enjoys. The same bean from the same farm can produce "
        "radically different cups depending only on how long it spent in the "
        "roaster. This is why two coffees can taste so different even when nothing "
        "about the bean itself has changed."
    ),
    "rc_questions": [
        {"stem": "What does the passage primarily describe?",
         "options": [
             "The economic value of the coffee industry",
             "The process by which raw coffee beans become the coffee people drink",
             "The differences between coffee shops in various countries",
             "The health effects of drinking coffee"
         ], "correct": 1},
        {"stem": "According to the passage, what is a 'coffee cherry'?",
         "options": [
             "A type of dessert made with coffee",
             "The red fruit that contains coffee beans",
             "A specific roasting technique",
             "A coffee shop chain"
         ], "correct": 1},
        {"stem": "In the third paragraph, the word 'bolder' most nearly means:",
         "options": ["braver", "stronger in flavour", "more colourful", "less expensive"], "correct": 1},
        {"stem": "What can be inferred about two cups of coffee made from beans grown on the same farm?",
         "options": [
             "They will always taste identical.",
             "They can taste very different depending on how the beans were roasted.",
             "They will both taste bitter.",
             "Only the price will differ between them."
         ], "correct": 1},
        {"stem": "The author's tone when discussing light versus dark roasts is best described as:",
         "options": [
             "strongly in favour of dark roasts",
             "strongly in favour of light roasts",
             "neutral, treating both as valid choices",
             "critical of all coffee roasting practices"
         ], "correct": 2},
    ]
},

{
    "title": "Why Workplaces Are Going Paperless",
    "topic": "office technology",
    "body": (
        "A decade ago, the average office desk was buried under paper. Printed "
        "memos, signed forms, file folders, and stacks of contracts were a normal "
        "part of working life. Today, in most knowledge-work industries, that "
        "paper has nearly vanished. Documents are written, edited, signed, and "
        "stored entirely online. Filing cabinets, once a fixture of every office, "
        "are now harder to find than meeting rooms."
        "\n\n"
        "Several forces drove the change. Cloud storage made it cheap to keep "
        "millions of documents available to anyone in the company within seconds. "
        "Electronic signature tools removed the last reason most contracts had "
        "to be printed at all. Remote and hybrid work, which expanded sharply "
        "after 2020, made physical paper actively inconvenient — a document on a "
        "shared drive can be opened by a colleague three time zones away, while "
        "a printed copy on someone's desk cannot."
        "\n\n"
        "The shift has not been entirely smooth. Older employees sometimes find "
        "digital workflows harder to learn than the paper systems they replaced. "
        "Security has become a more complex problem: a stolen folder used to "
        "contain a few dozen documents, while a stolen password can expose "
        "hundreds of thousands. And some industries with strict regulations, such "
        "as law and healthcare, still rely on paper for specific situations where "
        "the rules have not yet caught up with the technology. Even so, the "
        "direction is clear. The paperless office is no longer a prediction; for "
        "most workers, it is the daily reality."
    ),
    "rc_questions": [
        {"stem": "What is the main point of the passage?",
         "options": [
             "Most offices today still rely heavily on printed paper.",
             "The paperless office has largely arrived in most knowledge-work industries.",
             "Older employees prefer digital systems over paper.",
             "Cloud storage is the only reason for the shift away from paper."
         ], "correct": 1},
        {"stem": "According to the passage, what role did remote work play in the paperless shift?",
         "options": [
             "It made paper documents more useful.",
             "It made physical paper inconvenient because colleagues might be far apart.",
             "It had no effect on paper usage.",
             "It caused most companies to print more documents."
         ], "correct": 1},
        {"stem": "In the third paragraph, the word 'expose' most nearly means:",
         "options": ["protect", "make accessible to others", "destroy", "duplicate"], "correct": 1},
        {"stem": "What can be inferred about industries like law and healthcare?",
         "options": [
             "They have completely abandoned paper.",
             "They use less paper than other industries.",
             "Their use of paper is shaped by regulations that have not fully adapted to digital tools.",
             "They have no security concerns about digital documents."
         ], "correct": 2},
        {"stem": "The author's stance on the paperless office is best described as:",
         "options": [
             "skeptical that it has actually happened",
             "alarmed about the security risks involved",
             "matter-of-fact, presenting it as the established reality",
             "nostalgic for the era of paper documents"
         ], "correct": 2},
    ]
},

{
    "title": "How UPI Changed the Way India Pays",
    "topic": "technology and economy",
    "body": (
        "A decade ago, paying for almost anything in India meant handling "
        "cash. Even small shopkeepers preferred notes and coins, partly out "
        "of habit and partly because the alternatives were inconvenient. "
        "Credit and debit cards required machines that small vendors did not "
        "want to buy. Bank transfers were possible but slow, often taking a "
        "full day to reach the recipient. For everyday purchases — vegetables, "
        "auto-rickshaw fares, tea from a roadside stall — cash was the only "
        "real option."
        "\n\n"
        "The launch of the Unified Payments Interface, known as UPI, changed "
        "this picture rapidly. Introduced in 2016 by a body set up jointly by "
        "India's banks, UPI allowed any person with a smartphone and a bank "
        "account to send money to any other person almost instantly, free of "
        "charge. The system did not require a card machine, a special app from "
        "a particular bank, or even a long account number. A short identifier "
        "or a scanned QR code was enough. Within a few years, the small shops "
        "that had once accepted only cash were displaying QR codes next to "
        "their counters, and customers were paying for a cup of tea by tapping "
        "their phones."
        "\n\n"
        "The scale of the shift surprised even its designers. By 2024, India "
        "was processing more digital payments than the United States, China, "
        "and several European countries combined. Tax collection became easier "
        "because more transactions left a record. Small vendors found it "
        "easier to track their own daily takings. The change has not been "
        "uniformly smooth — older users have struggled with the technology, "
        "and frauds targeting first-time digital users have grown — but the "
        "shift itself appears irreversible. A country that, within living "
        "memory, ran almost entirely on cash now runs largely without it."
    ),
    "rc_questions": [
        # Cause and effect (instead of a generic main-idea Q1)
        {"stem": "According to the passage, what made UPI succeed where earlier alternatives to cash had failed?",
         "options": [
             "It was made compulsory by the government.",
             "It was free, instant, and required no special equipment for the vendor.",
             "It offered cashback rewards to every user.",
             "It worked only at large stores and chains."
         ], "correct": 1},
        # Detail
        {"stem": "Before UPI was introduced, why did small vendors generally not accept card payments?",
         "options": [
             "Because their customers preferred to pay with checks",
             "Because card machines were equipment small vendors did not want to buy",
             "Because the government had banned card use in small shops",
             "Because card payments were considered impolite"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the final paragraph, the word 'irreversible' most nearly means:",
         "options": [
             "easy to undo",
             "unlikely to go backwards",
             "growing more slowly",
             "limited to one region"
         ], "correct": 1},
        # Inference about the trade-offs
        {"stem": "What can be inferred about the costs of the UPI shift?",
         "options": [
             "There have been no negative consequences at all.",
             "Some groups, including older users, have found the change difficult, and new types of fraud have emerged.",
             "The shift has hurt every category of user equally.",
             "Most people regret the move away from cash."
         ], "correct": 1},
        # Author's stance / tone
        {"stem": "How would the author's overall view of UPI's impact best be described?",
         "options": [
             "Strongly opposed to the shift away from cash",
             "Genuinely impressed by the scale of the change while acknowledging real costs",
             "Indifferent to whether the shift continues",
             "Convinced that UPI will soon be replaced"
         ], "correct": 1},
    ]
},

{
    "title": "The Significance of the Indian Monsoon",
    "topic": "geography and culture",
    "body": (
        "To outsiders, the Indian monsoon often sounds like a dramatic weather "
        "event — a few months of heavy rain that the country must endure each "
        "year. From inside India, the picture is very different. The monsoon "
        "is not simply weather. It is the rhythm against which much of the "
        "country's economic life, food production, and cultural calendar is "
        "set."
        "\n\n"
        "The economic dependence is the most measurable. Roughly half of "
        "India's farmland still relies on rainfall rather than irrigation, "
        "which means a strong monsoon supports the harvests on which millions "
        "of farming households depend. A weak monsoon, by contrast, lowers "
        "rural incomes, raises food prices, and slows demand for everything "
        "from tractors to mobile phones in towns whose customers are mostly "
        "farmers. Economists routinely revise their growth forecasts up or "
        "down based on monsoon predictions. Few other countries of comparable "
        "size have an annual weather event so closely tied to national "
        "economic performance."
        "\n\n"
        "The agricultural impact runs deeper than the harvest in any one "
        "season. The timing of the rains determines what farmers plant and "
        "when. A delayed monsoon can shorten the growing window so much that "
        "an entire crop choice has to change. An early monsoon can damage "
        "fields that were not yet ready. Generations of farmers have built "
        "knowledge about local rain patterns into their planting calendars, "
        "and even small shifts in those patterns require painful adjustment."
        "\n\n"
        "What surprises many visitors most is how deeply the monsoon is "
        "woven into India's festivals and emotional life. Songs, films, and "
        "poetry have associated the first rains with renewal, longing, and "
        "celebration for centuries. Festivals such as Onam in Kerala, Teej "
        "in northern India, and Bonalu in Telangana fall during or just "
        "after the monsoon and treat the rains as something to be welcomed "
        "rather than merely tolerated. The monsoon, in this fuller sense, is "
        "not an inconvenience the country lives through. It is one of the "
        "structures around which Indian life is organized."
    ),
    "rc_questions": [
        # Best paraphrase of the central claim — varied from a generic main-idea Q1
        {"stem": "Which of the following best paraphrases the central argument of the passage?",
         "options": [
             "The monsoon is mainly a tourism attraction in India.",
             "The monsoon shapes Indian economic, agricultural, and cultural life, not just the country's weather.",
             "The monsoon causes more harm than benefit each year.",
             "Only farmers care about whether the monsoon arrives on time."
         ], "correct": 1},
        # Detail / specific information about the economic link
        {"stem": "According to the passage, why is the monsoon so closely watched by economists?",
         "options": [
             "Because the government collects taxes only during monsoon months",
             "Because about half of India's farmland depends on rainfall rather than irrigation",
             "Because monsoon damage is the largest cost to the central budget",
             "Because international trade is paused during the monsoon"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the third paragraph, the phrase 'painful adjustment' suggests that:",
         "options": [
             "farmers physically suffer because of weather changes",
             "shifts in rain patterns force farmers to make difficult and costly changes to their plans",
             "the government punishes farmers who do not adapt",
             "adjustments are made easily and without complaint"
         ], "correct": 1},
        # Inference about the cultural section
        {"stem": "What can be inferred from the passage's discussion of festivals such as Onam, Teej, and Bonalu?",
         "options": [
             "These festivals were created mainly to attract tourists.",
             "Indian culture has historically treated the monsoon as something to celebrate, not just endure.",
             "These festivals are observed only by people in farming communities.",
             "These festivals are a recent invention with no deeper history."
         ], "correct": 1},
        # Author's overall stance
        {"stem": "How would the author most likely describe the monsoon's place in Indian life?",
         "options": [
             "A periodic disturbance that interrupts normal life",
             "A natural event around which much of economic, agricultural, and cultural life is organized",
             "An outdated concept that no longer matters in modern India",
             "A purely religious phenomenon with little practical impact"
         ], "correct": 1},
    ]
},

{
    "title": "Saving the Royal Bengal Tiger",
    "topic": "wildlife conservation",
    "body": (
        "In the early 1970s, the Royal Bengal Tiger appeared to be heading "
        "toward extinction. Hunting, often celebrated as a sport during the "
        "colonial period, had reduced India's tiger population to fewer than "
        "two thousand. Forests that had once held large numbers of tigers "
        "had been cleared for farms and timber, leaving the remaining "
        "animals scattered across shrinking patches of land. In 1973, the "
        "Indian government launched Project Tiger, a national programme that "
        "set aside protected reserves where tigers and their prey could "
        "recover."
        "\n\n"
        "By many measures, the project has been a real success. India today "
        "is home to roughly seventy percent of the world's wild tigers, and "
        "the population has risen substantially over the past two decades. "
        "Forests inside the reserves have been better protected than those "
        "outside them, and as a result they have continued to support not "
        "only tigers but a wide range of other species — leopards, deer, "
        "elephants, and many smaller animals that share the same habitat. "
        "Few conservation programmes anywhere in the world can point to "
        "results on this scale."
        "\n\n"
        "The success has come with a difficult tension that the original "
        "programme did not fully anticipate. Many of the reserves were "
        "created on land where rural communities had lived for generations. "
        "These communities were sometimes relocated, sometimes restricted "
        "from grazing their cattle or collecting firewood inside the "
        "reserves. The rules were intended to protect the tigers, but they "
        "fell hardest on people who had few other ways to make a living. "
        "Where the relocations were carried out with proper compensation "
        "and consultation, communities have generally accepted the change. "
        "Where they were rushed or poorly handled, resentment has lingered "
        "for decades."
        "\n\n"
        "The challenge facing Project Tiger today is no longer simply how to "
        "protect tigers. It is how to protect them in a way that also treats "
        "the people living alongside them fairly. Newer initiatives have "
        "begun involving local communities in conservation work, sharing "
        "tourism revenue with nearby villages, and recognising that "
        "long-term success depends on the support of the people who live "
        "closest to the reserves. The tiger has been brought back from the "
        "edge. Keeping it there will require keeping its human neighbours on "
        "the same side."
    ),
    "rc_questions": [
        # Comparison/contrast — successes vs challenges, the central structure
        {"stem": "How does the passage characterize Project Tiger?",
         "options": [
             "As a complete failure that should be replaced",
             "As a programme with real conservation success and a difficult human cost that has had to be addressed over time",
             "As a perfectly designed programme that has had no negative consequences",
             "As an old initiative that no longer matters in modern India"
         ], "correct": 1},
        # Detail
        {"stem": "According to the passage, what proportion of the world's wild tigers live in India today?",
         "options": ["About one-third", "About half", "About seventy percent", "Almost all"], "correct": 2},
        # Vocabulary in context
        {"stem": "In the third paragraph, the word 'lingered' most nearly means:",
         "options": [
             "disappeared quickly",
             "continued for a long time",
             "spread to other regions",
             "appeared suddenly"
         ], "correct": 1},
        # Function of a sentence
        {"stem": "Why does the author distinguish between relocations carried out 'with proper compensation and consultation' and those that were 'rushed or poorly handled'?",
         "options": [
             "To suggest that all relocations were carried out the same way",
             "To make the point that the harm depended heavily on how the relocations were managed, not just on the fact that they happened",
             "To argue that no relocations should ever take place",
             "To blame the original communities for their own resentment"
         ], "correct": 1},
        # Inference about the path forward
        {"stem": "What can be inferred about the future direction of tiger conservation, based on the final paragraph?",
         "options": [
             "Conservation work will increasingly be done without involving local communities.",
             "Long-term success will depend on building cooperation with the people who live near the reserves.",
             "Project Tiger will likely be ended in the next few years.",
             "Tigers will be moved out of India to safer countries."
         ], "correct": 1},
    ]
},

{
    "title": "The Lifeline of India",
    "topic": "transportation and society",
    "body": (
        "Few institutions are as central to daily life in India as its "
        "railway network. Roughly twenty-three million passengers travel by "
        "train in the country every day — a number larger than the entire "
        "population of several mid-sized nations. The network stretches "
        "across more than seventy thousand kilometres of track and reaches "
        "into towns and villages that most other forms of transport never "
        "served. For more than a century, Indians have called it the "
        "lifeline of the nation, and the phrase is closer to literal "
        "description than poetic exaggeration."
        "\n\n"
        "The railway's origins lie in the colonial period, when British "
        "administrators built the first lines in the 1850s mainly to move "
        "raw materials from inland regions to coastal ports. The system "
        "they left behind, however, became something they had not "
        "intended. After independence, the network was steadily expanded "
        "and reshaped to serve Indian travellers rather than colonial "
        "trade. Lines that had ended at port cities were extended inland; "
        "schedules were rebuilt around the journeys ordinary citizens "
        "actually wanted to take. What began as a tool of extraction was "
        "gradually turned into a tool of national connection."
        "\n\n"
        "The scale of the operation is difficult to picture. Indian "
        "Railways employs over a million people, making it one of the "
        "largest employers in the world. It runs trains that travel for "
        "more than two days from one end of the country to the other, "
        "passing through climates that range from snow-covered mountains "
        "to tropical coasts. Tickets are priced so that long-distance "
        "travel remains within reach of working-class families, and the "
        "system loses money on most of its routes precisely because the "
        "government considers affordable travel a public service rather "
        "than a commercial product."
        "\n\n"
        "What surprises many first-time visitors is the social atmosphere "
        "inside the trains themselves. Long-distance journeys often last "
        "twenty-four hours or more, and during that time strangers in the "
        "same compartment routinely share meals, stories, and recommended "
        "stops. Vendors walk through the carriages selling tea, snacks, and "
        "regional specialties at almost every station. The journey, in "
        "many ways, is part of the destination. To travel by Indian "
        "railway is not simply to be moved from one place to another. It "
        "is to spend time inside one of the country's largest shared "
        "spaces."
    ),
    "rc_questions": [
        # Best paraphrase — testing the three-part central claim
        {"stem": "Which of the following best paraphrases the central argument of the passage?",
         "options": [
             "Indian Railways is mainly important because it makes a profit each year.",
             "Indian Railways shapes daily life in India through its sheer scale, its history, and the social experience of riding it.",
             "Indian Railways was a more impressive system before independence than after.",
             "Indian Railways should be replaced by faster forms of transport."
         ], "correct": 1},
        # Cause and effect — historical shift
        {"stem": "According to the passage, how did the purpose of the railway network change after independence?",
         "options": [
             "It was reduced in size to save money.",
             "It was reshaped from a system built for colonial trade into one built around the needs of Indian travellers.",
             "It was sold to private companies.",
             "It was kept exactly the same as it had been under colonial rule."
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the third paragraph, the word 'precisely' is used to suggest that:",
         "options": [
             "the system makes losses by accident, not by design",
             "the system makes losses for the specific reason that affordable travel is treated as a public service",
             "the system has never made any losses at all",
             "the system loses money only in specific seasons"
         ], "correct": 1},
        # Function of a paragraph
        {"stem": "What is the main purpose of the final paragraph?",
         "options": [
             "To argue that long-distance trains should be made faster",
             "To describe the social experience of travelling by train, treating the journey itself as part of the value",
             "To complain about overcrowding in Indian trains",
             "To compare Indian trains with trains in other countries"
         ], "correct": 1},
        # Author's stance
        {"stem": "How would the author most likely describe the role of Indian Railways in the country?",
         "options": [
             "An outdated system that has lost its importance",
             "A central institution whose value goes well beyond simply moving passengers from place to place",
             "A purely commercial business focused on profits",
             "A regional service that matters only in certain states"
         ], "correct": 1},
    ]
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

    {
    "title": "The Decline of the General-Purpose Newspaper",
    "topic": "media and society",
    "body": (
        "For much of the twentieth century, the daily newspaper occupied a "
        "remarkable position. A single publication offered local news, "
        "international affairs, sports, business, weather, classified "
        "advertisements, opinion essays, comic strips, and obituaries. Readers "
        "with very different interests subscribed to the same paper, and the "
        "advertising revenue from any one section helped subsidize the "
        "production of the others. This bundling was not incidental — it was "
        "structurally essential to the economic model that allowed serious "
        "journalism to be produced at scale."
        "\n\n"
        "The internet dismantled this model section by section. Classified "
        "advertisements migrated to specialized sites that did the job better "
        "and for free. Sports coverage moved to dedicated outlets that updated "
        "in real time. Weather, once a small but reliable reason to buy a "
        "morning paper, became a free utility on every smartphone. As each "
        "section was peeled away, the cross-subsidy that supported expensive "
        "investigative reporting weakened. By the 2010s, many newspapers had "
        "either closed entirely or had been hollowed out into thin shells of "
        "their former selves."
        "\n\n"
        "Some commentators have welcomed the change, arguing that specialized "
        "outlets serve their audiences better than a generalist newspaper ever "
        "could. There is some truth to this, but it overlooks what is lost when "
        "a single publication no longer brings disparate readers into contact "
        "with the same set of facts. A reader who came to the newspaper for "
        "sports might glance at the front page on the way and absorb something "
        "about local politics. A reader who came for the political coverage "
        "might find an obituary that gave context to a community they thought "
        "they understood. The newspaper's incidental virtues were the kind that "
        "do not appear in any business analysis but become visible only after "
        "they are gone."
    ),
    "rc_questions": [
        # Comparison
        {"stem": "How does the passage compare the newspaper's traditional model to today's specialized outlets?",
         "options": [
             "Specialized outlets are presented as wholly inferior to traditional newspapers.",
             "Specialized outlets serve audiences efficiently but lose the cross-section exposure newspapers provided.",
             "Both are described as equally effective at producing serious journalism.",
             "Specialized outlets are described as more profitable but less popular."
         ], "correct": 1},
        # Function of a sentence
        {"stem": "Why does the author describe the bundled newspaper model as 'structurally essential' rather than incidental?",
         "options": [
             "To suggest the newspaper industry was poorly managed",
             "To emphasize that the bundle wasn't just convenient — the cross-subsidy actually paid for serious journalism",
             "To argue that bundling should be illegal",
             "To explain why newspapers were popular with advertisers"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the second paragraph, 'hollowed out' most nearly means:",
         "options": [
             "physically emptied of furniture and staff",
             "reduced to a much weaker version of what they had been",
             "made completely inaccessible online",
             "purchased by a foreign owner"
         ], "correct": 1},
        # What would the author likely agree with
        {"stem": "The author would most likely agree with which of the following?",
         "options": [
             "The internet has made journalism objectively better in every respect.",
             "Newspapers should never have allowed classified ads to appear online.",
             "Some valuable functions of the old newspaper are difficult to replicate in a fragmented media environment.",
             "Generalist publications will return to their former dominance within a decade."
         ], "correct": 2},
        # Tone
        {"stem": "The author's tone in the final sentence is best described as:",
         "options": [
             "triumphant",
             "quietly elegiac",
             "bitterly accusatory",
             "coldly statistical"
         ], "correct": 1},
    ]
},

{
    "title": "The Global Rise of Street Food",
    "topic": "food and culture",
    "body": (
        "For most of the twentieth century, restaurant critics and travel "
        "guides treated street food with a familiar mixture of caution and "
        "condescension. Eating from a cart or a stall was assumed to be a "
        "compromise — affordable, certainly, and possibly authentic, but "
        "rarely something a serious diner would seek out. The unspoken "
        "assumption was that proper cuisine required walls, table service, "
        "and a certain financial threshold. Below that threshold, food was "
        "merely fuel."
        "\n\n"
        "That hierarchy has collapsed remarkably quickly. Within roughly two "
        "decades, street food has moved from the margins of culinary "
        "respectability to its center. Michelin guides now recognize hawker "
        "stalls in Singapore and roadside vendors in Bangkok. Television "
        "programs that once celebrated only fine dining now devote entire "
        "seasons to night markets and food trucks. A growing number of chefs "
        "trained in formal kitchens have left them voluntarily to set up "
        "operations that resemble, in everything but the prices, the carts "
        "their grandparents might have run."
        "\n\n"
        "Several forces converged to produce this shift. Travel writing, "
        "long dominated by a narrow set of European reference points, opened "
        "up to writers from Asia, Latin America, and Africa who took for "
        "granted that the best food in their cities had always been served "
        "outdoors. Social media accelerated this reframing by making vivid "
        "images of stall-cooked dishes visible to audiences far from the "
        "vendors themselves. Perhaps most importantly, a generation of diners "
        "raised on the rhetoric of authenticity grew skeptical of the elaborate "
        "rituals of formal dining. To them, a vendor who had cooked the same "
        "dish for thirty years carried a credibility that no tasting menu "
        "could replicate."
        "\n\n"
        "Whether this revaluation will prove durable is harder to predict. "
        "Some critics worry that the same forces lifting street food into "
        "global view may distort it — pricing out original customers, "
        "encouraging vendors to perform for cameras rather than feed neighbors, "
        "or attracting investment that erodes the very informality that made "
        "the food appealing in the first place. The recognition is real, but "
        "so are the costs of being noticed."
    ),
    "rc_questions": [
        # Best paraphrase
        {"stem": "Which of the following best paraphrases the central argument of the passage?",
         "options": [
             "Street food is healthier than food served in formal restaurants.",
             "Street food has shifted from being dismissed to being celebrated, though this recognition carries risks.",
             "Michelin guides should stop recognizing street food vendors.",
             "Social media is the most important reason street food became popular."
         ], "correct": 1},
        # Function of a sentence
        {"stem": "Why does the author note that some chefs trained in formal kitchens have voluntarily moved to street food operations?",
         "options": [
             "To suggest that formal kitchens treat their chefs poorly",
             "To illustrate how thoroughly the prestige hierarchy between formal and street food has been overturned",
             "To argue that street food chefs earn more money than restaurant chefs",
             "To criticize chefs for abandoning their training"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the first paragraph, the word 'condescension' most nearly means:",
         "options": [
             "approval mixed with envy",
             "an attitude that treats something as beneath serious consideration",
             "scientific curiosity",
             "polite indifference"
         ], "correct": 1},
        # Cause and effect
        {"stem": "According to the passage, what role did travel writers from outside Europe play in the shift?",
         "options": [
             "They translated existing European food guides into other languages.",
             "They brought a perspective in which outdoor cooking was already understood as central to good cuisine.",
             "They campaigned for Michelin to recognize street food.",
             "They invented the concept of authenticity in food writing."
         ], "correct": 1},
        # Author would agree with
        {"stem": "The author would most likely agree with which of the following statements?",
         "options": [
             "The recent recognition of street food is wholly beneficial for the vendors involved.",
             "Street food's growing prestige has both genuine value and unintended downsides.",
             "Formal dining will eventually disappear entirely.",
             "Street food was always recognized as superior; only critics were slow to admit it."
         ], "correct": 1},
    ]
},
{
    "title": "The Science of Procrastination",
    "topic": "psychology",
    "body": (
        "Procrastination is often dismissed as a simple failure of willpower — "
        "evidence that the procrastinator is lazy, undisciplined, or "
        "insufficiently motivated. This explanation has the appeal of "
        "simplicity, but psychologists have spent the last several decades "
        "dismantling it. Research now points to a more layered account in "
        "which willpower plays a smaller role than most people assume."
        "\n\n"
        "The first refinement came from work on emotional regulation. "
        "Researchers observed that people rarely procrastinate on tasks they "
        "find pleasant, regardless of how difficult those tasks are. The "
        "consistent predictor was not difficulty but emotional aversion — "
        "tasks that produced anxiety, boredom, or self-doubt. Procrastination, "
        "in this view, is less a failure to act than a strategy for avoiding "
        "an unpleasant feeling. Delay buys temporary relief from the negative "
        "emotion the task provokes, even though the relief is purchased at "
        "the cost of greater stress later."
        "\n\n"
        "A second line of research extended this picture by examining how "
        "people forecast their future selves. Studies consistently show that "
        "individuals imagine their future selves as calmer, more disciplined, "
        "and better resourced than they currently feel. A task that feels "
        "unbearable today seems, in the imagination, manageable next Tuesday. "
        "This systematic miscalibration helps explain why procrastinators "
        "are often genuinely surprised when next Tuesday arrives and the task "
        "feels just as unbearable as it did before. The future self that was "
        "supposed to handle it never materializes; only the present self ever "
        "does."
        "\n\n"
        "The most recent refinement concerns identity. Researchers have begun "
        "to argue that chronic procrastinators often think of themselves as "
        "procrastinators in a way that becomes self-reinforcing. Once the "
        "label is internalized, individual delays no longer feel like choices "
        "to be examined; they feel like expressions of who one is. Breaking "
        "the pattern, on this account, requires more than scheduling tools "
        "or motivation techniques. It requires loosening an identity that has "
        "come to incorporate the very behavior the person hopes to change. "
        "None of these three explanations rules the others out. Most "
        "procrastinators, the literature suggests, are caught in some "
        "combination of all three."
    ),
    "rc_questions": [
        # Logical sequence — testing whether the candidate tracks the order of explanations
        {"stem": "In what order does the passage present its explanations of procrastination?",
         "options": [
             "Identity, then emotional regulation, then forecasting errors",
             "Emotional regulation, then forecasting errors about the future self, then identity",
             "Willpower failure, then identity, then emotional regulation",
             "Forecasting errors, then willpower failure, then emotional regulation"
         ], "correct": 1},
        # Detail tied to the sequence — second explanation
        {"stem": "According to the passage, what specifically do studies on future-self forecasting reveal?",
         "options": [
             "People accurately predict how they will feel next Tuesday.",
             "People imagine their future selves as more capable and calmer than they currently feel.",
             "People rarely think about their future selves at all.",
             "People predict their future selves will be more anxious than their present selves."
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the second paragraph, the phrase 'emotional aversion' refers to:",
         "options": [
             "a strong dislike or discomfort produced by certain tasks",
             "a fear of failure shared by all procrastinators",
             "a medical condition that prevents productivity",
             "the inability to feel any emotion at all"
         ], "correct": 0},
        # Cause and effect — connects two explanations
        {"stem": "How does the third explanation (identity) build on the earlier two?",
         "options": [
             "It rejects the earlier explanations as outdated.",
             "It claims emotional and forecasting patterns can solidify into a self-concept that perpetuates procrastination.",
             "It argues that identity is the only true cause of procrastination.",
             "It suggests that identity-based procrastination disappears once the person uses scheduling tools."
         ], "correct": 1},
        # Author would agree with — synthesizes the whole sequence
        {"stem": "Which of the following statements would the author of the passage most likely endorse?",
         "options": [
             "Procrastination is fundamentally a problem of poor willpower.",
             "The three explanations described are competing theories, only one of which is correct.",
             "Procrastination usually involves overlapping causes that operate together rather than alone.",
             "Identity-based explanations have made the earlier theories obsolete."
         ], "correct": 2},
    ]
},

{
    "title": "The Evolution of Remote Work",
    "topic": "workplace and economy",
    "body": (
        "The story of remote work is sometimes told as a triumph and "
        "sometimes as a disaster, depending on who is doing the telling. "
        "Both accounts contain enough truth to be plausible, and both omit "
        "enough to be misleading. A more careful look reveals a transition "
        "whose costs and benefits are distributed unevenly — and whose "
        "ultimate balance depends on the metric one chooses to weigh."
        "\n\n"
        "On the side of the optimists, the evidence is genuinely impressive. "
        "Surveys of knowledge workers consistently report higher job "
        "satisfaction, better work-life balance, and reduced commuting stress "
        "since the shift began. Studies measuring individual output have, in "
        "many sectors, found that remote workers complete the same volume of "
        "tasks as office-based peers, sometimes more. Companies that embraced "
        "distributed teams have widened their hiring pools dramatically, "
        "drawing talent from regions that previously could not realistically "
        "supply major employers. By many of the measures companies and "
        "workers themselves prioritize, remote work has been a clear "
        "improvement."
        "\n\n"
        "On the side of the skeptics, the evidence is no less serious — but "
        "it operates on a different timescale. Mentorship, the skeptics argue, "
        "depends on incidental contact: junior employees absorb judgment by "
        "watching senior ones handle situations in real time, and that "
        "transmission breaks down when work happens primarily in scheduled "
        "video calls. The cultural and creative side of organizations may "
        "also suffer in ways that show up only after several years, not in "
        "any quarterly productivity figure. Skeptics worry less about whether "
        "tasks get completed and more about whether the deeper organizational "
        "capacities that produced those tasks in the first place are quietly "
        "eroding."
        "\n\n"
        "What makes the debate hard to resolve is that the two sides are "
        "not really measuring the same thing. Optimists measure outputs that "
        "are visible immediately and easy to count. Skeptics measure capacities "
        "that are invisible until they are needed and difficult to quantify "
        "even then. Neither side is wrong, exactly. But anyone trying to "
        "decide policy from the available data should be honest that the "
        "evidence on one side is sturdier-looking only because that side has "
        "chosen the easier evidence to gather."
    ),
    "rc_questions": [
        # Comparison/contrast — direct test of the central structure
        {"stem": "How does the passage characterize the difference between the optimist and skeptic positions on remote work?",
         "options": [
             "The optimists are correct and the skeptics are simply nostalgic.",
             "The two sides emphasize evidence operating on different timescales and measuring different things.",
             "Both sides have produced equally rigorous evidence with no meaningful differences.",
             "The skeptics have produced more careful evidence than the optimists."
         ], "correct": 1},
        # Detail from one side of the comparison
        {"stem": "According to the passage, what specifically do skeptics worry about that does not appear in productivity figures?",
         "options": [
             "Whether workers complete enough tasks each week",
             "Whether the broader organizational capacities behind productive work are quietly weakening",
             "Whether companies can afford fewer office leases",
             "Whether commuting times will return to pre-pandemic levels"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the third paragraph, the word 'incidental' most nearly means:",
         "options": [
             "deliberately scheduled",
             "happening in passing rather than as part of a planned activity",
             "unimportant and easily skipped",
             "occurring only in emergencies"
         ], "correct": 1},
        # Function of a paragraph — testing why the final paragraph exists
        {"stem": "What is the primary function of the fourth paragraph?",
         "options": [
             "To declare the optimists the winners of the debate",
             "To explain why the two sides reach different conclusions even when both are reasoning carefully",
             "To introduce a new study that resolves the dispute",
             "To dismiss both sides as equally biased"
         ], "correct": 1},
        # Author would agree with — synthesizes the comparison
        {"stem": "The author would most likely agree with which of the following claims?",
         "options": [
             "Remote work is a clear improvement and the skeptics' concerns can be dismissed.",
             "Easier-to-measure evidence is not necessarily more important — it is just easier to gather.",
             "Companies should stop allowing any form of remote work.",
             "The remote-work debate has been definitively settled in favor of the skeptics."
         ], "correct": 1},
    ]
},
{
    "title": "Traditional Knowledge and Modern Development in India",
    "topic": "environment and policy",
    "body": (
        "In several Indian states, decisions about land, forests, and water "
        "increasingly involve a tension that earlier generations of planners "
        "rarely had to consider seriously. On one side stands the framework "
        "of modern development — measurable yields, scalable infrastructure, "
        "and projects evaluated by economic return. On the other stands what "
        "researchers now call traditional ecological knowledge: the "
        "site-specific understanding accumulated over generations by "
        "communities who have lived with particular landscapes, soils, and "
        "monsoon patterns. The two frameworks were once treated as obvious "
        "competitors, with the modern approach assumed to be the more "
        "credible one. That assumption is being revisited."
        "\n\n"
        "The shift began with a series of expensive failures. Large dams "
        "displaced communities and silted up faster than engineers had "
        "predicted. Monoculture forestry projects, planted on land local "
        "communities had managed for centuries through rotation and selective "
        "harvest, produced timber but degraded the soil and lost much of the "
        "biodiversity that had quietly supported nearby agriculture. Modern "
        "irrigation schemes, designed without reference to seasonal patterns "
        "the local population had observed for hundreds of monsoons, sometimes "
        "delivered water at the wrong time of year. The pattern was not that "
        "modern engineering was wrong, but that engineering deployed without "
        "local knowledge tended to overlook variables that mattered."
        "\n\n"
        "Researchers studying these failures began to examine what "
        "communities themselves had been doing before the projects arrived. "
        "What they found was rarely a single body of teachings, and it was "
        "almost never written down. It was instead a distributed system of "
        "rules, observations, and informal experiments — when to plant, "
        "where not to clear, which trees signaled an underground spring, "
        "which crops should follow which others. Some of this knowledge "
        "turned out, on closer examination, to encode genuine ecological "
        "principles that modern science had also independently discovered. "
        "Some of it did not. The challenge is that distinguishing the two "
        "requires careful study rather than blanket faith in either tradition "
        "or modernity."
        "\n\n"
        "What is emerging in policy circles is therefore neither a return "
        "to the past nor a continuation of the recent past. It is an attempt "
        "to integrate two ways of knowing that, for most of the last century, "
        "barely communicated with each other. The integration is uncomfortable "
        "for partisans of both sides. It asks engineers to take seriously "
        "knowledge that was not produced through their methods, and it asks "
        "communities to allow their accumulated wisdom to be examined rather "
        "than simply accepted. Where this integration is being attempted "
        "carefully, the early results suggest that the two frameworks correct "
        "each other's blind spots. Where it is being attempted as a political "
        "gesture, it tends to satisfy neither side."
    ),
    "rc_questions": [
        # Identifying the author's stance — central to this focus
        {"stem": "Which of the following best describes the author's overall position on the relationship between traditional knowledge and modern development?",
         "options": [
             "Traditional knowledge has proven superior to modern engineering and should replace it.",
             "Modern engineering remains the more credible framework, with traditional knowledge offering little of substance.",
             "Both frameworks have genuine value, and integrating them carefully tends to produce better outcomes than either alone.",
             "The two frameworks are fundamentally incompatible and cannot be combined."
         ], "correct": 2},
        # Cause and effect — what triggered the shift
        {"stem": "According to the passage, what initially caused planners to take traditional ecological knowledge more seriously?",
         "options": [
             "A government policy that mandated it",
             "A series of large modern development projects that produced unexpected failures",
             "Pressure from international environmental organizations",
             "The publication of a single influential book"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the third paragraph, the word 'distributed' most nearly means:",
         "options": [
             "given out for free to everyone",
             "spread across many people and practices rather than held in one place",
             "distributed by a government agency",
             "translated into multiple languages"
         ], "correct": 1},
        # Best paraphrase — tests precise restating of a nuanced claim
        {"stem": "Which best restates the author's claim about the value of traditional ecological knowledge?",
         "options": [
             "All traditional practices have been validated by modern science.",
             "Traditional knowledge is always wrong and should be disregarded.",
             "Some traditional practices encode genuine ecological principles, but determining which ones requires careful examination.",
             "Traditional knowledge is essentially the same as modern science under a different name."
         ], "correct": 2},
        # Inference about the final paragraph
        {"stem": "What can be inferred from the contrast in the final two sentences between integration done 'carefully' and integration done 'as a political gesture'?",
         "options": [
             "Political gestures are always more effective than careful study.",
             "The integration of two frameworks succeeds when it is genuine and substantive, but fails when it is performed without serious effort.",
             "Politicians should be excluded from environmental decisions.",
             "Careful integration is impossible in democratic societies."
         ], "correct": 1},
    ]
},

{
    "title": "The Hyper-Urbanization of Tier-2 Cities",
    "topic": "urban development",
    "body": (
        "For most of the past three decades, public discussion of Indian "
        "urbanization has focused almost entirely on the metros — Mumbai, "
        "Delhi, Bangalore, Chennai, Hyderabad. The growth of these cities was "
        "treated as the central story of the country's economic rise, and the "
        "smaller cities that surrounded them were assumed to be either "
        "stagnant or quietly emptying as their younger residents moved to the "
        "metros for work. This picture, never entirely accurate, has now "
        "become substantially misleading."
        "\n\n"
        "Tier-2 cities — Pune, Coimbatore, Indore, Jaipur, Lucknow, "
        "Bhubaneswar, and several dozen others — have grown faster, in "
        "percentage terms, than the metros for nearly fifteen years. Some of "
        "this growth is explained by the saturation of the metros themselves. "
        "Real estate prices in Bangalore and Mumbai eventually pushed both "
        "companies and skilled workers to look elsewhere. Improved internet "
        "infrastructure made it possible to run a business or a remote "
        "workforce from cities that had previously offered no such option. "
        "What began as a quiet redistribution has become, in some regions, a "
        "genuine reversal of the metro-centric pattern."
        "\n\n"
        "The standard interpretation of this trend is celebratory. A more "
        "balanced distribution of economic activity, the argument goes, "
        "relieves pressure on overburdened metros and spreads opportunity to "
        "regions that were previously left behind. There is real truth here. "
        "But the celebratory framing tends to assume that tier-2 cities are "
        "growing by replicating the metros' development model on a smaller "
        "scale, and this assumption deserves more scrutiny than it has "
        "received. The infrastructure of most tier-2 cities — their drainage, "
        "their road networks, their housing stock, their power supply — was "
        "built for populations a fraction of their current size. Many are now "
        "absorbing in five years what the metros absorbed over thirty, "
        "without the gradual institutional learning that the metros, for all "
        "their failings, had time to undertake."
        "\n\n"
        "The unintended consequences are starting to become visible. Cities "
        "with no historical experience of high-rise construction are "
        "permitting it on streets that cannot support the resulting traffic "
        "or sewage loads. Local governments that previously managed modest "
        "budgets are negotiating with developers many times their size, "
        "often without the legal capacity to extract meaningful concessions. "
        "Air quality, once a metro problem, has begun to deteriorate sharply "
        "in cities whose residents had assumed the issue would never reach "
        "them. None of this means the rise of tier-2 cities is a bad thing. "
        "It means the celebratory framing has obscured the question of "
        "whether these cities are being given the institutional support to "
        "manage what is, for them, a transformation without precedent."
    ),
    "rc_questions": [
        # Identifying assumptions — the central focus
        {"stem": "Which assumption underlying the 'celebratory' interpretation does the author most directly challenge?",
         "options": [
             "That tier-2 cities are growing at all",
             "That tier-2 cities are simply repeating the metros' development pattern at a smaller scale",
             "That metro cities are still economically important",
             "That economic distribution should be balanced across regions"
         ], "correct": 1},
        # Cause and effect — what's driving the growth
        {"stem": "According to the passage, what factors helped trigger the faster growth of tier-2 cities?",
         "options": [
             "Government incentives that explicitly subsidized tier-2 relocation",
             "Saturation of the metros combined with improved internet infrastructure",
             "A decline in skilled workers in the metros",
             "Foreign investment that bypassed the metros entirely"
         ], "correct": 1},
        # Vocabulary in context
        {"stem": "In the third paragraph, the word 'scrutiny' most nearly means:",
         "options": [
             "celebration without question",
             "careful examination",
             "financial investment",
             "political opposition"
         ], "correct": 1},
        # Best paraphrase of a nuanced claim
        {"stem": "Which of the following best restates the author's concern about tier-2 cities' infrastructure?",
         "options": [
             "Tier-2 cities should stop growing until their infrastructure improves.",
             "Tier-2 cities are absorbing rapid growth using infrastructure built for much smaller populations, and without the gradual learning that metros had time for.",
             "Tier-2 cities have better infrastructure than the metros.",
             "Infrastructure problems are unique to one or two specific cities."
         ], "correct": 1},
        # Inference about the author's overall stance
        {"stem": "What can be inferred about the author's view of the rise of tier-2 cities?",
         "options": [
             "It is fundamentally a negative development that should be reversed.",
             "It is a positive development whose risks are being hidden by overly optimistic framing.",
             "It is an irrelevant trend that does not deserve attention.",
             "It is identical in every way to the earlier rise of the metros."
         ], "correct": 1},
    ]
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
     # Grammar — intermediate (7 new)
    {"type": "grammar", "stem": "Each of the candidates _____ asked to bring two copies of their CV.",
    "options": ["were", "are", "is", "have been"], "correct": 2},

    {"type": "grammar", "stem": "She _____ working at this company since 2019.",
    "options": ["is", "has been", "was", "had been"], "correct": 1},

    {"type": "grammar", "stem": "Neither the manager nor the team members _____ available this afternoon.",
    "options": ["is", "are", "was", "has been"], "correct": 1},

    {"type": "grammar", "stem": "I would rather you _____ that report by Friday.",
    "options": ["finish", "finished", "will finish", "have finished"], "correct": 1},

    {"type": "grammar", "stem": "The instructions were so unclear that hardly anyone _____ them.",
    "options": ["understand", "understood", "had understood", "understands"], "correct": 1},

    {"type": "grammar", "stem": "There _____ a few errors in the document that need correcting.",
    "options": ["is", "are", "was", "has"], "correct": 1},

    {"type": "grammar", "stem": "He told me _____ worry about the deadline.",
    "options": ["don't", "to not", "not to", "no"], "correct": 2},

    # Vocabulary — intermediate (6 new)
    {"type": "vocabulary", "stem": "Choose the word closest in meaning to 'verify':",
    "options": ["assume", "confirm", "ignore", "decide"], "correct": 1},

    {"type": "vocabulary", "stem": "In the sentence 'The proposal was met with widespread approval', 'widespread' most nearly means:",
    "options": ["limited", "extensive", "delayed", "private"], "correct": 1},

    {"type": "vocabulary", "stem": "Which word best completes the sentence: 'The trainer was patient and ____ when answering questions.'",
    "options": ["thorough", "thoughtless", "thoughtful", "thrifty"], "correct": 2},

    {"type": "vocabulary", "stem": "Which word is the OPPOSITE of 'temporary'?",
    "options": ["brief", "permanent", "frequent", "occasional"], "correct": 1},

    {"type": "vocabulary", "stem": "Choose the word closest in meaning to 'reluctant':",
    "options": ["eager", "unwilling", "confused", "exhausted"], "correct": 1},

    {"type": "vocabulary", "stem": "In the sentence 'She gave a candid review of the project', 'candid' most nearly means:",
    "options": ["dishonest", "honest and direct", "lengthy", "secret"], "correct": 1},

    # Fill-blank — intermediate (5 new)
    {"type": "fill_blank", "stem": "We need to find a solution _____ works for everyone involved.",
    "options": ["who", "which", "whose", "whom"], "correct": 1},

    {"type": "fill_blank", "stem": "The presentation was _____ interesting that nobody left early.",
    "options": ["so", "such", "very", "too"], "correct": 0},

    {"type": "fill_blank", "stem": "He prefers tea _____ coffee in the morning.",
    "options": ["than", "from", "to", "over"], "correct": 2},

    {"type": "fill_blank", "stem": "Despite _____ tired, she completed the report on time.",
    "options": ["was", "be", "being", "to be"], "correct": 2},

    {"type": "fill_blank", "stem": "If you have any questions, _____ free to email me.",
    "options": ["fell", "feel", "felt", "feeling"], "correct": 1},
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

        # Grammar — expert (7 new)
    {"type": "grammar", "stem": "Which sentence uses the conditional correctly?",
    "options": [
        "Had I known about the change, I would have prepared differently.",
        "If I would have known about the change, I would have prepared differently.",
        "If I knew about the change, I would have prepared differently.",
        "Had I knew about the change, I would have prepared differently."
    ], "correct": 0},

    {"type": "grammar", "stem": "Identify the sentence with the misplaced modifier:",
    "options": [
        "She read in the newspaper that the company had merged.",
        "She nearly drove her children to school every day.",
        "I almost spent the entire afternoon revising the document.",
        "The candidate, who had three years of experience, accepted the offer."
    ], "correct": 1},

    {"type": "grammar", "stem": "Which sentence is grammatically correct?",
    "options": [
        "The data suggests that customer demand has been falling.",
        "The data suggest that customer demand has been falling.",
        "Both A and B are acceptable depending on usage.",
        "Neither A nor B is correct."
    ], "correct": 2},

    {"type": "grammar", "stem": "Choose the option with correct subject-verb agreement:",
    "options": [
        "A number of issues was raised during the meeting.",
        "A number of issues were raised during the meeting.",
        "The number of issues were raised during the meeting.",
        "Number of issues are raised during the meeting."
    ], "correct": 1},

    {"type": "grammar", "stem": "Which sentence correctly uses the past perfect tense?",
    "options": [
        "By the time the auditor arrived, the team submitted the documents.",
        "By the time the auditor arrived, the team had submitted the documents.",
        "By the time the auditor had arrived, the team submitted the documents.",
        "By the time the auditor will arrive, the team had submitted the documents."
    ], "correct": 1},

    {"type": "grammar", "stem": "Identify the sentence with correct semicolon usage:",
    "options": [
        "The product launched in March; sales exceeded expectations.",
        "The product launched in March, sales exceeded expectations.",
        "The product launched in March; and sales exceeded expectations.",
        "The product launched in March: sales exceeded expectations."
    ], "correct": 0},

    {"type": "grammar", "stem": "Which sentence uses 'whom' correctly?",
    "options": [
        "Whom is responsible for this decision?",
        "The candidate whom we interviewed yesterday accepted the role.",
        "Whom called you this morning?",
        "Tell me whom is in charge."
    ], "correct": 1},

    # Vocabulary — expert (6 new)
    {"type": "vocabulary", "stem": "Choose the word closest in meaning to 'preclude':",
    "options": ["enable", "prevent", "encourage", "delay"], "correct": 1},

    {"type": "vocabulary", "stem": "In a professional context, 'reconcile' two figures most nearly means:",
    "options": ["to argue about them", "to make them agree or balance", "to ignore them", "to publish them publicly"], "correct": 1},

    {"type": "vocabulary", "stem": "Which word best fits: 'The findings, while interesting, are not _____ enough to change policy.'",
    "options": ["substantive", "substantial", "subjective", "subsidiary"], "correct": 1},

    {"type": "vocabulary", "stem": "Which word is closest in meaning to 'incentivize'?",
    "options": ["punish", "encourage through reward", "delay through bureaucracy", "explain in detail"], "correct": 1},

    {"type": "vocabulary", "stem": "Choose the word that best replaces 'precipitate' in: 'The announcement may precipitate a sharp drop in share prices.'",
    "options": ["prevent", "trigger", "soften", "explain"], "correct": 1},

    {"type": "vocabulary", "stem": "Which word is closest in meaning to 'warrant' (as a verb)?",
    "options": ["to question", "to justify or call for", "to refuse", "to repeat"], "correct": 1},

    # Fill-blank — expert (5 new)
    {"type": "fill_blank", "stem": "The proposal will be reviewed _____ its merits, not on the seniority of who submitted it.",
    "options": ["for", "by", "on", "at"], "correct": 2},

    {"type": "fill_blank", "stem": "Several employees raised concerns _____ the new policy at the all-hands meeting.",
    "options": ["of", "for", "about", "from"], "correct": 2},

    {"type": "fill_blank", "stem": "The company is committed _____ maintaining the highest ethical standards.",
    "options": ["in", "on", "to", "for"], "correct": 2},

    {"type": "fill_blank", "stem": "_____ all the difficulties the project faced, the final outcome exceeded expectations.",
    "options": ["Although", "Despite", "However", "Whereas"], "correct": 1},

    {"type": "fill_blank", "stem": "The senior leadership team is comprised _____ representatives from each region.",
    "options": ["of", "from", "with", "by"], "correct": 0},
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
