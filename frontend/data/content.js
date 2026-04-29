/* =========================================================================
   content.js — sample test content. In production this comes from a backend.
   We use a JS file (not JSON) so the pages work over the file:// protocol.
   ========================================================================= */

window.TEST_CONTENT = {

  // ---------- READING SECTION ----------
  reading: {
    durationSeconds: 25 * 60, // 25 minutes
    passage: {
      title: "The Rise of Renewable Energy",
      paragraphs: [
        "Over the past two decades, renewable energy sources have shifted from the periphery of global power generation to the centre of energy strategy. Once dismissed as expensive or unreliable, technologies such as solar, wind, and hydroelectric power now account for a steadily growing share of electricity worldwide. By 2024, renewables generated more than thirty per cent of global electricity, a figure that would have seemed implausible at the start of the millennium.",
        "Several factors explain this transformation. The cost of photovoltaic modules has fallen by more than eighty per cent since 2010, driven by economies of scale, manufacturing improvements, and aggressive policy support in countries such as Germany, China, and the United States. Wind turbines have grown larger and more efficient, capturing energy from sites that would have been considered marginal a generation ago. Battery storage, while still costly, has begun to make intermittent generation more viable by smoothing the supply during periods of low sun or wind.",
        "Yet the transition is far from complete. Fossil fuels still supply the majority of the world's primary energy, and many developing nations face a difficult dilemma: they need rapid increases in power generation to support economic growth, but the cheapest immediate option is often coal. International climate agreements have attempted to address this imbalance through financial transfers and technology sharing, but progress has been uneven. Critics argue that wealthy nations, having industrialised on the back of fossil fuels, cannot reasonably demand that poorer ones forego the same path without offering substantial assistance.",
        "Another challenge lies in the structure of existing power grids. Most were designed around a small number of large, centralised plants delivering electricity in one direction to consumers. Renewables, by contrast, are often distributed: thousands of rooftop solar arrays, dozens of wind farms, even individual homes feeding power back into the system. Adapting the grid to handle this complexity requires investment, regulatory reform, and new technical standards. In some regions, transmission bottlenecks have become a serious obstacle, with newly built solar capacity sitting idle for years while waiting for grid connections.",
        "Despite these obstacles, the direction of travel seems clear. Investors increasingly view fossil fuel assets as carrying long-term financial risk, while renewable projects benefit from declining costs and supportive regulation. Whether the transition occurs quickly enough to meet climate targets remains an open question, but the technological and economic groundwork has been laid. The decisions made in the coming decade — by governments, companies, and consumers — will determine the pace.",
      ],
    },
    questions: [
      {
        id: "Q1",
        stem: "According to the passage, the cost of solar panels has fallen primarily because of:",
        options: [
          "Government bans on competing energy sources",
          "Economies of scale, manufacturing improvements, and policy support",
          "A worldwide reduction in electricity demand",
          "A decline in the quality of raw materials",
        ],
        answer: 1,
      },
      {
        id: "Q2",
        stem: "The author's overall tone in the passage is best described as:",
        options: [
          "Skeptical and dismissive",
          "Analytical and measured",
          "Sarcastic and playful",
          "Apologetic and uncertain",
        ],
        answer: 1,
      },
      {
        id: "Q3",
        stem: "Why do many developing nations face a 'difficult dilemma' according to the passage?",
        options: [
          "Their citizens prefer fossil fuels for cultural reasons",
          "They cannot manufacture solar panels themselves",
          "They need rapid power growth, and coal is often the cheapest option",
          "International law forbids them from using renewables",
        ],
        answer: 2,
      },
      {
        id: "Q4",
        stem: "The phrase 'transmission bottlenecks' in paragraph 4 most likely refers to:",
        options: [
          "Delays in delivering oil to power plants",
          "Limits in the grid that prevent new renewable capacity from being used",
          "Legal disputes over land use",
          "Shortages of qualified electrical engineers",
        ],
        answer: 1,
      },
      {
        id: "Q5",
        stem: "Which statement best summarises the role of battery storage as described?",
        options: [
          "It has fully solved the intermittency problem",
          "It is irrelevant to renewable adoption",
          "It is becoming more useful but is still expensive",
          "It is only used in developing nations",
        ],
        answer: 2,
      },
      {
        id: "Q6",
        stem: "The author argues that wealthy nations:",
        options: [
          "Should immediately stop all financial transfers",
          "Cannot fairly demand poorer nations skip fossil fuels without offering help",
          "Have already solved their own emissions problem",
          "Were the first to invest in solar power",
        ],
        answer: 1,
      },
      {
        id: "Q7",
        stem: "According to the passage, fossil fuel assets are increasingly viewed by investors as:",
        options: [
          "A safer long-term store of value",
          "Subject to long-term financial risk",
          "Equivalent in risk to renewable projects",
          "More attractive due to recent regulation",
        ],
        answer: 1,
      },
      {
        id: "Q8",
        stem: "The main idea of the final paragraph is that:",
        options: [
          "The energy transition has already finished",
          "Climate targets will certainly be met on time",
          "The groundwork is laid; the speed of change depends on near-term decisions",
          "Governments have stopped supporting renewables",
        ],
        answer: 2,
      },
    ],
  },

  // ---------- SPEAKING SECTION ----------
  speaking: {
    durationSeconds: 2 * 60, // 2 minutes max recording
    prepSeconds: 30,
    topics: [
      "Describe a place that has had a significant impact on your life. Explain why it matters to you and what you have learned from it.",
      "Talk about a skill you would like to learn in the next five years. Why have you chosen it, and how would you go about learning it?",
      "Some people prefer working in teams; others prefer working alone. Which do you prefer and why?",
      "Describe a book, film, or piece of music that changed how you think about something. Explain what changed and why.",
      "Discuss a challenge you have faced in your education or career. How did you respond, and what did you learn?",
      "If you could solve one global problem, which would you choose and how would you approach it?",
      "Describe a person you admire who is not a celebrity. What qualities make them admirable?",
      "Some say technology is making people more isolated. Do you agree or disagree, and why?",
    ],
  },
};
