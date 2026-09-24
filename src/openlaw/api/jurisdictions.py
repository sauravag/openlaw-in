"""The allowlist of Indian states and union territories.

`openlaw.api.schemas.AskRequest.state` is interpolated verbatim into
`openlaw.answers.escalation.escalation_footer` ("contact the {state} State
Legal Services Authority"), which is rendered to the user. An unvalidated
`state` therefore lets a caller put arbitrary text into the one sentence that
tells a citizen who to call for help -- confirmed by the Task 4 review
(finding H2) with a working proof string that landed a `<script>` tag and a
phishing link inside a real response body. This module exists so
`AskRequest` can reject anything that is not a real jurisdiction at the
schema boundary, rather than trust free text this far into a safety-critical
sentence.

28 states + 8 union territories, current as of the Jammu & Kashmir
Reorganisation Act, 2019 (J&K and Ladakh as separate union territories).
"""

INDIAN_STATES: frozenset[str] = frozenset(
    {
        "Andhra Pradesh",
        "Arunachal Pradesh",
        "Assam",
        "Bihar",
        "Chhattisgarh",
        "Goa",
        "Gujarat",
        "Haryana",
        "Himachal Pradesh",
        "Jharkhand",
        "Karnataka",
        "Kerala",
        "Madhya Pradesh",
        "Maharashtra",
        "Manipur",
        "Meghalaya",
        "Mizoram",
        "Nagaland",
        "Odisha",
        "Punjab",
        "Rajasthan",
        "Sikkim",
        "Tamil Nadu",
        "Telangana",
        "Tripura",
        "Uttar Pradesh",
        "Uttarakhand",
        "West Bengal",
    }
)

INDIAN_UNION_TERRITORIES: frozenset[str] = frozenset(
    {
        "Andaman and Nicobar Islands",
        "Chandigarh",
        "Dadra and Nagar Haveli and Daman and Diu",
        "Delhi",
        "Jammu and Kashmir",
        "Ladakh",
        "Lakshadweep",
        "Puducherry",
    }
)

#: The full allowlist `AskRequest.state` is validated against.
INDIAN_STATES_AND_UTS: frozenset[str] = INDIAN_STATES | INDIAN_UNION_TERRITORIES
