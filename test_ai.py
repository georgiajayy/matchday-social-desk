"""
test_ai.py
Quick check that the project can talk to the AI model on Microsoft Foundry.
Run:  python test_ai.py
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads the secret values from the .env file

endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
client = OpenAI(
    base_url=f"{endpoint}/openai/v1/",
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

response = client.chat.completions.create(
    model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
    messages=[
        {"role": "system", "content": "You are a lively football commentator. Reply in one sentence."},
        {"role": "user", "content": "Darnell Hayes has just scored from 2 metres to put North West Wonders "
                                    "1-0 up against South East Eagles in the 11th minute. Describe it."},
    ],
)

print("Connected to Microsoft Foundry!")
print(response.choices[0].message.content)
