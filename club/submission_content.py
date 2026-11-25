"""Shared training submission content and code language definitions."""

CODE_LANGUAGES = {
    "text": "纯文本",
    "cpp": "C++",
    "c": "C",
    "python": "Python",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "csharp": "C#",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "sql": "SQL",
    "bash": "Shell",
}

DATA_STRUCTURE_VALID_SUBMISSIONS = 2
DATA_STRUCTURE_CODE_POINTS = 2
DATA_STRUCTURE_BLOG_POINTS = 2
DATA_STRUCTURE_RULE_FIELDS = (
    "data_structure_valid_submissions", "data_structure_code_points", "data_structure_blog_points",
)


# Display placeholders are never submitted solution content.
EMPTY_LOGIC_TEXTS={'未提交题解思路。','未提交题解思路.','未提交题解思路'}


def normalize_logic(text):
    text=text.strip()
    return '' if text in EMPTY_LOGIC_TEXTS else text
