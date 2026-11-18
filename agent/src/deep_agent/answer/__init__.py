"""
The final answer: the `submit_answer` format, keeping it bound to SQL that actually ran, and what the user sees.

- schema.py        the submit_answer tool format (+ clarification / validation_response variants)
- verification.py  did the answer's SQL really run? -> final_result (matched / re_executed / ...)
- middleware.py    answer recovery, submit reminder, invalid-submission retry, final_result at the end of a turn
- render.py        the Hebrew markdown shown to the user
"""
