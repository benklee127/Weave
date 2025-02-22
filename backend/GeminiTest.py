from google import genai
import os
from dotenv import load_dotenv
from google.genai import types


load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

generation_config = {
  "temperature": 1,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 8192,
  "response_mime_type": "application/json",
  "system_instruction": "while maintaining the exact position of each element in the array, translate the plaintext items to chinese\n",
}

response = client.models.generate_content(
    model='gemini-2.0-flash-001', 
    contents='high',
    config=generation_config
)
print(response.text)