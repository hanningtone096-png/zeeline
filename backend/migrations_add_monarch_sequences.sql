CREATE TABLE IF NOT EXISTS monarch_policy_sequences (
    policy_class VARCHAR(20) PRIMARY KEY,
    last_seq     INT NOT NULL
);

INSERT INTO monarch_policy_sequences (policy_class, last_seq) VALUES
    ('private',    533143),
    ('commercial', 12717)
ON DUPLICATE KEY UPDATE last_seq = last_seq;