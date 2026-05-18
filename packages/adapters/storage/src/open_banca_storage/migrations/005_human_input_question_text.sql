-- Question text for operator UX when prompt_user pauses (terminal + GET /jobs/{id}).

ALTER TABLE human_input_answers ADD COLUMN question_text TEXT;
