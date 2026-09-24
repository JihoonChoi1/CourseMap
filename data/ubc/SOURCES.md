# UBC Real-Data Transcription Record (Phase 6)

Records, course by course, where `data/ubc/catalog.json`, `programs.json`, and `students.json` were sourced from, and what
was dropped or approximated when translating UBC's rules into the engine model (CNF prereq/corequisite, list of offered
seasons, ALL / PICK_N groups).
The engine model itself was not extended (Phase 6 decision: ignore or approximate, and record everything).

## 1. Sources and principles

### 1.1 Sources (all accessed 2026-09-27)

| Content | URL |
|---|---|
| Course descriptions/prerequisites (CPSC) | https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv |
| Course descriptions/prerequisites (MATH) | https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv |
| Course descriptions/prerequisites (STAT) | https://vancouver.calendar.ubc.ca/course-descriptions/subject/statv |
| Degree requirements (BSc Major in Computer Science, 0376) | https://vancouver.calendar.ubc.ca/faculties-colleges-and-schools/faculty-science/bachelor-science/computer-science |
| CPSC offering terms (department timetable, 2026W and 2025W) | https://www.cs.ubc.ca/courses (select session via `?session=2025W`) |
| MATH offering terms (department offering history) | https://www.math.ubc.ca/undergraduate/courses/historical-overview-courses-offered |

Reference academic year: Academic Calendar 2026/27. The calendar's course code `CPSC_V 110` (Vancouver campus) was written as `CPSC 110`.

### 1.2 Collection method

Manual transcription. Pages were fetched with curl and read as text; the JSON was hand-written (there is no script that turns a page into JSON).
The "Original text" quotes in §6 below were copied verbatim from the fetched page text, to avoid introducing typos when transcribing.

### 1.3 Term-offering rules

- Term system: `seasons = ["W1", "W2"]` (Winter Term 1: Sept-Dec, Term 2: Jan-Apr). **Summer is excluded**
  (Phase 0 put summer sessions out of scope). Year is the UBC session year: `2026 W2` means Jan-Apr 2027 (= 2026W Term 2).
- CPSC: a term counts as "offered" if the department timetable shows it offered in **either 2025W or 2026W**.
  Why not just 2026W: the 2026W listing shows only a Term 2 section for CPSC 110/210 (2025W has three Term 1 sections each).
  Since the 2026W listing was fetched while Term 1 was still in progress, the Term 1 sections may simply be missing from it yet, so a single year's listing was not treated as proof that a course is "W2-only."
  Where the two years' listings differ, both years' values are recorded in §6's "Offered" line (110, 210, 410, 415, 417, 418, 423).
- MATH: from the department's offering-history page, 2022W-2026W. At the time of access, 2026W Term 2 had not yet been posted.
- **STAT 251: assumed.** The STAT department's course lookup page only showed "1 Section per year / Most Recent: 2026S1," so winter-term offering could not be confirmed. Since it's a required course, it was assumed to be offered both terms. If it is actually summer-only, results would change significantly.

### 1.4 Course selection (30 = 24 CPSC + 6 MATH/STAT)

- 8 required CPSC: 110, 121, 210, 213, 221, 310, 313, 320 from the Major requirements
- 16 elective CPSC: 300-400-level courses that can make up the 3 tracks. Only lecture-based courses per the calendar were chosen
  (436I, 448, 449, 455, 491, and CPSC 490 Student Directed Seminars are excluded from requirement calculations, so they were left out)
- 6 MATH/STAT: the Major's required courses (MATH 100, 101, 200, STAT 251) + the "MATH 111 or 221" pair of alternatives.
  The original text lists several alternatives for first-year calculus (102/104/180, etc.), but only one representative course was included for each

None of the 30 chosen courses' original text has a year-standing restriction ("third-year standing") or a "permission of department" condition.

## 2. Handling rules the model can't express

| UBC rule | Handling | Affected courses | Direction of effect on the result |
|---|---|---|---|
| Grade conditions ("minimum grade of 76%", "68% or higher") | **Ignored** | CPSC 415, CPSC 221 (b), MATH 100 | Looser than reality |
| High-school prerequisite courses (Pre-calculus 12, etc.) | **Dropped** (assumed completed) | CPSC 121, MATH 100 | Looser than reality |
| Credit-based requirements ("at least 3 credits from MATH/STAT 200-level") | **Approximated**: an OR clause over the matching in-catalog courses | CPSC 320 | Same, within the catalog |
| Out-of-catalog alternatives (DSCI, CPEN, ELEC, AI, other MATH/STAT, `_O` Okanagan courses) | **Dropped**: removed from the OR clause | Most courses | Stricter than reality (no other path remains) |
| Non-CNF expressions `A ∨ (B ∧ C)` | Converted to CNF after removing out-of-catalog courses. Applying the distributive law is an **exact conversion** | CPSC 330, 410, 423, 440 | Same, within the catalog |
| Equivalency / credit exclusion (MATH 111 ≡ MATH 221, CPSC 322 ≡ AI 322) | **Ignored** (both can be taken) | MATH 111, MATH 221, CPSC 322 | Looser than reality |
| Summer session | **Excluded** | All | Stricter than reality |
| Year-standing restrictions, permission of department | None present in the chosen courses | — | — |
| The 120 total credits, Science's common requirements (Foundational, Lab, Breadth, Arts, Upper-level), Communication, SCIE 113, free electives | **Excluded** (Phase 6 decision: CS-major requirements only) | — | — |
| STAT 251 substitution rule ("STAT 200 or 201 + MATH/STAT 302") | **Ignored** | STAT 251 | Stricter than reality |

## 3. Degree-requirement mapping (`programs.json` degree = `UBC_CS_MAJOR`)

| Calendar Major (0376) line | Group | Notes |
|---|---|---|
| CPSC 110, 121 (year 1) / 210, 213, 221 (year 2) / 310, 313, 320 (years 3-4) | `CPSC_CORE` ALL 8 courses | The "CPSC 110 (or 103 and 107)" alternative was dropped |
| MATH 100 or 102 or ..., MATH 101 or 103 or ..., MATH 200, STAT 251 | `MATH_CORE` ALL 4 courses | Representative course only |
| MATH 111 or 221 | `LINEAR_ALGEBRA` PICK_N 1 | |
| 9 credits of CPSC/AI 300+ + 9 credits of CPSC/AI 400+ | `UPPER_ELECTIVE` PICK_N 6 (16 courses, 300-400 level) + `UPPER_400` PICK_N 3 (9 courses, 400-level) | **Approximated**, see below |

"9 credits of 300+" and "9 credits of 400+" must be filled with different courses (18 credits total). Since the engine model
allows one course to satisfy several groups at once (Phase 0 §7-1), this was translated as "at least 3 of the 6 chosen
300+ courses must be 400-level." If every course is worth 3 credits, this matches the original rule exactly. Where a
4-credit course (CPSC 319, 415) is involved, it's counted by course count rather than credits, so it diverges slightly
from the original rule.
AI courses (AI 360, AI 422, etc.) were not added to the catalog.

## 4. Tracks (self-defined, not official UBC tracks)

The UBC Major has no job tracks (the only official option is "Option in Artificial Intelligence"). Three tracks were
defined to represent desired job paths. Track courses are all within the `UPPER_ELECTIVE` pool, so they also count toward
the 6-course major elective requirement.

| Track | Required (ALL) | Elective (PICK_N 2) |
|---|---|---|
| `AI_ML` AI/ML Engineer | CPSC 340 | CPSC 322, 423, 425, 440 |
| `SYSTEMS` Systems/Infrastructure Engineer | CPSC 317 | CPSC 415, 416, 417, 418 |
| `SOFTWARE` Software/Web Developer | CPSC 304 | CPSC 319, 344, 404, 410 |

## 5. Student scenarios (`students.json`)

| ID | Track | Situation | Current engine result (tests/test_ubc.py) |
|---|---|---|---|
| U1 | AI_ML | First-year, 8 terms starting 2026 W1, cap 15 | Feasible in 6 terms (fallback). Oracle minimum: 5 terms |
| U2 | SYSTEMS | Year-1 requirements done, 6 terms starting 2027 W1, cap 15 | Feasible in 5 terms (fallback). Oracle minimum: 4 terms |
| U3 | AI_ML | Done through year 2, took linear algebra as MATH 111. 4 terms starting 2028 W1 | Feasible in 3 terms (= oracle minimum). MATH 221 also gets scheduled, because of CPSC 340 |
| U4 | SOFTWARE | Like U3 but part-time (cap 6), 4 terms | Infeasible ("not proven"). The oracle also finds it infeasible in 4 terms, feasible in 5 |
| U5 | SOFTWARE | Final term, only math left (MATH 101, 200, STAT 251), 1 term starting 2029 W1 | Infeasible: `E_NOT_OFFERED` (MATH 101 is W2-only). Feasible in 3 terms |
| U6 | SYSTEMS | Year 4, 2 terms starting 2029 W2 | Feasible in 1 term |

UBC checks that a completed course's prerequisites were actually satisfied, but the engine trusts `completed` as given (Phase 0 §3).

## 6. Per-course record

### CPSC 110 — Computation, Programs, and Programming

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: no prerequisite or corequisite conditions
- Conversion: prereqs `[]`, coreqs `[]`
- Dropped or approximated: —
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 2 / 2025W: Term 1, Term 2

### CPSC 121 — Models of Computation

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: Principles of Mathematics 12 or Pre-calculus 12. Corequisite: One of CPSC 107, CPSC 110."
- Conversion: coreqs `[["CPSC 110"]]`
- Dropped or approximated: **Dropped**: prereq "Principles of Mathematics 12 or Pre-calculus 12" (high-school course, assumed completed). Dropped the coreq alternative CPSC 107 (out of catalog)
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 210 — Software Construction

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: One of CPSC 107, CPSC 110."
- Conversion: prereqs `[["CPSC 110"]]`
- Dropped or approximated: Dropped: CPSC 107
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 2 / 2025W: Term 1, Term 2

### CPSC 213 — Introduction to Computer Systems

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: All of CPSC 121, CPSC 210."
- Conversion: prereqs `[["CPSC 121"], ["CPSC 210"]]`
- Dropped or approximated: —
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 221 — Basic Algorithms and Data Structures

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: One of CPSC_V 210, CPEN_V 221 and either (a) one of CPSC_V 121, MATH_V 220, MATH_O 220 or (b) a score of 68% or higher in MATH_V 226."
- Conversion: prereqs `[["CPSC 210"], ["CPSC 121"]]`
- Dropped or approximated: Dropped: CPEN 221, MATH 220, MATH_O 220. **Ignored**: "(b) 68%+ in MATH 226" (grade condition + out of catalog)
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 310 — Introduction to Software Engineering

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: All of CPSC 213, CPSC 221."
- Conversion: prereqs `[["CPSC 213"], ["CPSC 221"]]`
- Dropped or approximated: —
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 313 — Computer Hardware and Operating Systems

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC_V 213 and either CPSC_V 221 or DSCI_V 221."
- Conversion: prereqs `[["CPSC 213"], ["CPSC 221"]]`
- Dropped or approximated: Dropped: DSCI 221
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 320 — Intermediate Algorithm Design and Analysis

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: All of (a) CPSC_V 221 or DSCI_V 221, (b) at least 3 credits from MATH_V or STAT_V at 200 level or above or MATH_O 200, MATH_O 220, MATH_O 222, MATH_O 225, MATH_O 307, STAT_O 121, STAT_O 203, STAT_O 205, STAT_O 230, STAT_O 303 or any course on the STAT_V 200 credit exclusion: (link omitted) or HES_O 340, HMKN_O 205, POLI_O 400, PSYO_O 271, STAT_O 124."
- Conversion: prereqs `[["CPSC 221"], ["MATH 200", "MATH 221", "STAT 251"]]`
- Dropped or approximated: **Approximated**: "(b) 3 credits of MATH/STAT 200-level or above" (credit-based) -> an OR over in-catalog MATH/STAT courses at 200-level or above. MATH 111 is 100-level, so excluded. Dropped: DSCI 221, `_O` (Okanagan) courses, the STAT 200 credit-exclusion list
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 304 — Introduction to Relational Databases

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC_V 221 or DSCI_V 221."
- Conversion: prereqs `[["CPSC 221"]]`
- Dropped or approximated: Dropped: DSCI 221
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 317 — Introduction to Computer Networking

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC_V 213 and either CPSC_V 221 or DSCI_V 221."
- Conversion: prereqs `[["CPSC 213"], ["CPSC 221"]]`
- Dropped or approximated: Dropped: DSCI 221
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 319 — Software Engineering Project

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: CPSC 310."
- Conversion: prereqs `[["CPSC 310"]]`
- Dropped or approximated: —
- Offered: `["W2"]` <- CS department timetable, 2026W: Term 2 / 2025W: Term 2

### CPSC 322 — Introduction to Artificial Intelligence

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either CPSC_V 221 or DSCI_V 221. Equivalency: AI_V 322."
- Conversion: prereqs `[["CPSC 221"]]`
- Dropped or approximated: Dropped: DSCI 221. The AI 322 equivalency is **ignored** (a substitute course)
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 330 — Applied Machine Learning

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either (a) one of CPSC_V 203, CPSC_V 210, CPEN_V 221, DSCI_V 221 or (b) MATH_V 210 and one of CPSC_V 107, CPSC_V 110."
- Conversion: prereqs `[["CPSC 210"]]`
- Dropped or approximated: For the non-CNF expression `(203|210|CPEN221|DSCI221) ∨ (MATH210 ∧ (107|110))`, removing out-of-catalog courses leaves (b) unsatisfiable (no MATH 210) -> only CPSC 210 remains
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 340 — Machine Learning and Data Mining

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: All of (a) one of CPSC_V 221, DSCI_V 221 (b) one of MATH_V 152, MATH_V 221, MATH_V 223, MATH_O 222 (c) one of MATH_V 200, MATH_V 217, MATH_V 226, MATH_V 253, MATH_V 254, MATH_O 200 (d) one of STAT_V 241, STAT_V 251, ECON_V 325, ECON_V 327, MATH_V 302, STAT_V 302, MATH_V 318, STAT_O 302."
- Conversion: prereqs `[["CPSC 221"], ["MATH 221"], ["MATH 200"], ["STAT 251"]]`
- Dropped or approximated: Dropped: DSCI 221, MATH 152/223/217/226/253/254, `_O` courses, STAT 241, ECON 325/327, MATH/STAT 302, MATH 318. **MATH 111 is absent from the original text** -> a student who took linear algebra as MATH 111 still needs MATH 221 (U3). The MATH 111/221 credit exclusion is ignored
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 344 — Introduction to Human Computer Interaction Methods

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: One of CPSC 210, CPEN 221."
- Conversion: prereqs `[["CPSC 210"]]`
- Dropped or approximated: Dropped: CPEN 221
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 404 — Advanced Relational Databases

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC 304 and one of CPSC 213, CPSC 261, CPEN 212."
- Conversion: prereqs `[["CPSC 304"], ["CPSC 213"]]`
- Dropped or approximated: Dropped: CPSC 261, CPEN 212
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 410 — Advanced Software Engineering

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either (a) CPSC 310 or (b) all of CPEN 321, CPEN 331."
- Conversion: prereqs `[["CPSC 310"]]`
- Dropped or approximated: For the non-CNF expression `310 ∨ (CPEN321 ∧ CPEN331)`, (b) is out of catalog -> CPSC 310
- Offered: `["W2"]` <- CS department timetable, 2026W: not listed / 2025W: Term 2

### CPSC 415 — Operating Systems Design and Implementation

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 4 (calendar lists 4)
- Original text: "Prerequisite: All of the following with a minimum grade of 76% in each of: (a) CPSC_V 310 or CPEN_V 321, (b) CPSC_V 313 or CPEN_V 331, and (c) CPSC_V 317 or ELEC_V 331."
- Conversion: prereqs `[["CPSC 310"], ["CPSC 313"], ["CPSC 317"]]`
- Dropped or approximated: **Ignored**: "minimum grade of 76% in each" (grade condition). Dropped: CPEN 321, CPEN 331, ELEC 331
- Offered: `["W1"]` <- CS department timetable, 2026W: Term 1 / 2025W: not listed

### CPSC 416 — Distributed Systems

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: One of CPSC 313, CPEN 331 and one of CPSC 317, ELEC 331."
- Conversion: prereqs `[["CPSC 313"], ["CPSC 317"]]`
- Dropped or approximated: Dropped: CPEN 331, ELEC 331
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 417 — Computer Networking

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: All of CPSC_V 313, CPSC_V 317."
- Conversion: prereqs `[["CPSC 313"], ["CPSC 317"]]`
- Dropped or approximated: —
- Offered: `["W2"]` <- CS department timetable, 2026W: Term 2 / 2025W: not listed

### CPSC 418 — Parallel Computation

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC 320 and one of CPSC 261, CPSC 313, CPEN 212, CPEN 411."
- Conversion: prereqs `[["CPSC 320"], ["CPSC 313"]]`
- Dropped or approximated: Dropped: CPSC 261, CPEN 212, CPEN 411
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1 / 2025W: Term 1, Term 2

### CPSC 423 — Natural Language Processing

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisites: Either CPSC_V 340 or both (a) one of AI_V 240, CPSC_V 330 and (b) one of STAT_V 200, STAT_V 251, ECON_V 325, ECON_V 327, MATH_V 302, STAT_V 302, MATH_V 318."
- Conversion: prereqs `[["CPSC 340", "CPSC 330"], ["CPSC 340", "STAT 251"]]`
- Dropped or approximated: **Non-CNF -> CNF, exact conversion**: for `340 ∨ ((AI240|330) ∧ (STAT200|251|…))`, removing out-of-catalog courses (AI 240, STAT 200, ECON, MATH 302, etc.) leaves `340 ∨ (330 ∧ STAT251)` = `(340 ∨ 330) ∧ (340 ∨ STAT251)` (distributive law, no approximation)
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: not listed

### CPSC 425 — Computer Vision

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: CPSC_V 221 and one of MATH_V 200, MATH_V 217, MATH_V 226, MATH_V 253, MATH_V 254 and one of MATH_V 111, MATH_V 131, MATH_V 152, MATH_V 221, MATH_V 223."
- Conversion: prereqs `[["CPSC 221"], ["MATH 200"], ["MATH 111", "MATH 221"]]`
- Dropped or approximated: Dropped: MATH 217/226/253/254, MATH 131/152/223
- Offered: `["W1", "W2"]` <- CS department timetable, 2026W: Term 1, Term 2 / 2025W: Term 1, Term 2

### CPSC 440 — Advanced Machine Learning

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/cpscv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either CPSC_V 340 or both (a) AI_V 240 and (b) one of STAT_V 251, ECON_V 325, ECON_V 327, MATH_V 302, STAT_V 302, MATH_V 318."
- Conversion: prereqs `[["CPSC 340"]]`
- Dropped or approximated: For the non-CNF expression `340 ∨ (AI240 ∧ (STAT251|…))`, AI 240 is out of catalog -> CPSC 340
- Offered: `["W2"]` <- CS department timetable, 2026W: Term 2 / 2025W: Term 2

### MATH 100 — Differential Calculus with Applications

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: A score of 80% or higher in BC Pre-calculus 12. High school calculus is strongly recommended."
- Conversion: prereqs `[]`, coreqs `[]`
- Dropped or approximated: **Dropped**: "80%+ in BC Pre-calculus 12" (high-school course + grade condition)
- Offered: `["W1", "W2"]` <- Math department offering history, Term 1: 2022W-2026W / Term 2: 2023W-2025W

### MATH 101 — Integral Calculus with Applications

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: One of MATH_V 100, MATH_V 102, MATH_V 104, MATH_V 110, MATH_V 120, MATH_V 180, MATH_V 184, MATH_O 100."
- Conversion: prereqs `[["MATH 100"]]`
- Dropped or approximated: Dropped: MATH 102/104/110/120/180/184, MATH_O 100
- Offered: `["W2"]` <- Math department offering history, Term 1: not offered / Term 2: 2022W-2025W

### MATH 111 — Matrix Algebra

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either (a) one of MATH_V 100, MATH_V 102, MATH_V 104, MATH_V 110, MATH_V 120, MATH_V 180, MATH_V 184, MATH_O 100 or (b) advanced credit for MATH_V 100, or (c) SCIE_V 001 as a co-requisite."
- Conversion: prereqs `[["MATH 100"]]`
- Dropped or approximated: Dropped: the other calculus alternatives, "(b) advanced credit," "(c) SCIE 001 coreq." The MATH 221 equivalency/credit-exclusion is **ignored**
- Offered: `["W1", "W2"]` <- Math department offering history, Term 1: 2025W, 2026W / Term 2: 2025W

### MATH 200 — Calculus III

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: One of MATH_V 101, MATH_V 103, MATH_V 105, MATH_V 121, SCIE_V 001, MATH_O 101, MATH_O 103."
- Conversion: prereqs `[["MATH 101"]]`
- Dropped or approximated: Dropped: MATH 103/105/121, SCIE 001, `_O` courses
- Offered: `["W1", "W2"]` <- Math department offering history, Term 1: 2022W-2026W / Term 2: 2022W-2025W

### MATH 221 — Matrix Algebra

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/mathv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: Either (a) one of MATH_V 100, MATH_V 102, MATH_V 104, MATH_V 110, MATH_V 120, MATH_V 180, MATH_V 184, SCIE_V 001, MATH_O 100 or (b) advanced credit for MATH_V 100, or (c) SCIE_V 001 as a corequisite."
- Conversion: prereqs `[["MATH 100"]]`
- Dropped or approximated: Dropped: the other calculus alternatives, advanced credit, SCIE 001. The MATH 111 equivalency/credit-exclusion is **ignored**
- Offered: `["W1", "W2"]` <- Math department offering history, Term 1: 2022W-2026W / Term 2: 2022W-2025W

### STAT 251 — Introductory Probability and Statistics

- Source: https://vancouver.calendar.ubc.ca/course-descriptions/subject/statv (accessed 2026-09-27)
- Credits: 3 (calendar lists 3)
- Original text: "Prerequisite: One of APSC_V 173, MATH_V 101, MATH_V 103, MATH_V 105, MATH_V 121, SCIE_V 001, MATH_O 101, MATH_O 103."
- Conversion: prereqs `[["MATH 101"]]`
- Dropped or approximated: Dropped: APSC 173, MATH 103/105/121, SCIE 001, `_O` courses
