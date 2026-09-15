from dotenv import load_dotenv
load_dotenv()

from strands import Agent
from strands.models.litellm import LiteLLMModel
from tools import count_weekly_enquiries_by_category, recommend_restock

model = LiteLLMModel(model_id="groq/openai/gpt-oss-20b")

agent3 = Agent(
    model=model,
    system_prompt=(
        "You are the restock recommendation agent for O.C. Manjos, an electrical "
        "merchant. Call count_weekly_enquiries_by_category to see enquiry volume "
        "by category, then for the Distribution Board category specifically, call "
        "recommend_restock with that count. State clearly whether the order was "
        "auto-approved or escalated for owner approval, the order value, and why. "
        "Use ₦ for currency."
        "You must call recommend_restock for Distribution Board every time, even if the count seems low — never answer without calling it. Do not estimate or guess results."
    ),
    tools=[count_weekly_enquiries_by_category, recommend_restock],
)

response = agent3("Check if Distribution Boards need restocking based on this week's enquiries.")
print(response)