# English Proficiency Test — Source-of-Truth Requirements

This document mirrors the requirements provided by the project owner. It is the authoritative spec; any code or planning decision that conflicts with this must be flagged and reconciled.

## Problem Statement
Recruiters need a scalable and objective way to evaluate candidate English proficiency (written and spoken) before interviews. Current approaches are manual, inconsistent, and hard to standardize.

## 1. Product Vision
Build an AI-based English assessment platform for HR teams to evaluate candidate proficiency across:
- Written English
- Spoken English
- Grammar and comprehension
- Vocabulary
- Communication fluency
- Role-level adaptive assessment (Intermediate / Expert)

Platform must generate tests dynamically using an LLM, evaluate responses automatically, and expose results only to administrators.

- **Written: 25 questions** (configurable; "default 15" appears later — needs clarification)
- **Spoken: 5 questions** (verbal)
- **URL valid for 24 hours** (configurable at link generation time)

## 2. User Roles

### A. Admin / Recruiter Portal
Users:
- HR Administrator
- Hiring / Recruiter Managers
- Candidates

Admin creates a candidate and generates the test link.

Admin can configure:
- Test Name
- Skill level: Intermediate or Advanced/Expert
- Number of questions (default 15)
- Time limit: 30 min Written + 30 min Spoken (adjustable)
- Sections to include (Written, Spoken, or both)

### B. Secure Test Link Generator
Admin clicks "Generate Assessment Link" → produces:
- Unique tokenized candidate URL
- One-time usable
- Expiry within 24 hours (configurable)
- Email delivery

## 3. Candidate Experience

Display sections:
- Written Assessment
- Speaking Assessment
- Total duration: 45 minutes (configurable)
- Default: 15 questions

### Written Assessment (LLM-generated)
Question types span:
- **Grammar:** sentence correction, fill in the blanks, error spotting
- **Reading Comprehension:** passage-based questions
- **Vocabulary:** synonyms, contextual usage
- **Writing Skill:** short email drafting, response writing
- **Scenario-based Communication**

Generated dynamically by LLM. Questions must not repeat.

### Speaking Assessment
Candidate records verbal responses. Sample prompts:
- Q1: Introduce yourself in 60 seconds
- Q2: Describe how you handled conflict in a team
- Q3: Explain a complex topic to a non-technical person

Capture: browser microphone → audio → speech-to-text.

Evaluation dimensions:
- Pronunciation
- Fluency
- Grammar
- Confidence
- Vocabulary richness

### Submission
Candidate sees only: "Thank you. Your assessment has been completed." No score shown.

## 4. AI / LLM Question Generation Engine

Prompt controls per difficulty:
- **Intermediate:** "Generate 15 English assessment questions for intermediate-level job candidates covering grammar, comprehension, and professional communication."
- **Expert:** "Generate advanced English assessment questions for senior professional candidates emphasizing analytical writing, business communication, and verbal fluency."

Parameters: difficulty, industry context, role-based vocabulary, adaptive questioning.

Models: Claude.

## 5. AI Evaluation Engine

### Written Test Scoring (out of 100)

| Parameter | Weight |
|---|---|
| Grammar | 20% |
| Vocabulary | 20% |
| Comprehension | 20% |
| Writing Quality | 20% |
| Professional Communication | 20% |

### Spoken Test Scoring (out of 100)

| Parameter | Weight |
|---|---|
| Pronunciation | 20% |
| Fluency | 25% |
| Grammar | 20% |
| Vocabulary | 15% |
| Confidence | 20% |

Combined score generated.

## 6. Final Admin Report

Example:
- Candidate: John Smith
- Written: 82/100
- Speaking: 76/100
- Overall: 79%
- Rating: Recommended / Borderline / Not Recommended

AI feedback narrative covers strengths and gaps.
Visible only to admin.

## 7. Admin Dashboard

Widgets:
- Tests Sent
- Tests Completed
- Candidate Scores
- Average Pass Rate
- Skill Distribution
- Downloadable Reports
- Candidate details list (pass/fail)

Filters: Role, Client, Date, Score Range.

## 8. Suggested Architecture

| Layer | Suggested |
|---|---|
| Frontend (Candidate + Admin Portal) | Angular |
| Backend Services | .NET |
| Database | PostgreSQL |
| LLM | Claude |
| Speech AI | Whisper |

Backend modules:
- Authentication
- Candidate Management
- Test Engine
- LLM Question Generator
- Speech Evaluation Engine
- Scoring Engine
- Reporting Module

DB tables:
- Candidates
- Assessments
- QuestionBank
- Responses
- SpeechTranscripts
- Scores
- AdminUsers

## 9. Workflow
Admin creates test → System generates secure link → Candidate takes written + spoken test → AI evaluates → Scores stored → Admin reviews.

## 10. Talent Recommendation
Based on score: Hire / Interview / Training needed.

## 11. Screen List

Admin: Login, Dashboard, Create Assessment, Candidate Link Generator, Results View, Reports.

Candidate: Test Link landing, Written Test, Speaking Test, Submission Confirmation.
