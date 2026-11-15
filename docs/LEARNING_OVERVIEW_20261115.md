# Stored-answer learning overview

GET `/api/v4/users/{user_id}/learning-overview?day=YYYY-MM-DD` returns a typed Swagger schema: user_id, as_of, source=stored_answers, history_quiz_limit=90, streak_days, attempt_count, correct_count, review_topic_count and per-topic counts/guide/review date.

The latest answered attempt per topic determines unresolved review. An unanswered quiz never clears an older error. A corrected topic does not hide another unresolved topic. Review becomes eligible the next calendar day; the daily-quiz endpoint still decides which eligible topic is selected. This is not a guarantee that every pending topic is issued that date.

Streak counts consecutive answer dates ending today, or yesterday if today has not yet been answered. Queries exclude future answers and other users. No new model request or quiz issuance is performed by overview. Existing data need no migration. The quiz response adds a concept hint derived from its topic; answers and explanations remain hidden until submission.

Validation: 17 targeted tests passed for overview, history and durable state; the new tests cover corrected vs unresolved topics, historical queries, isolated users, streak gaps, invalid dates, Swagger schema and no AI invocation. No keys, model/provider destinations, Discord settings or financial source data changed. Observation snapshot remains 2026-11-04.
