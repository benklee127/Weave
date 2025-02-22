from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
import json


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


app = Flask(__name__)
CORS(app)

@app.route("/")
def home():
	return "Hello, world!"

@app.route("/test", methods=["POST"])
def test():
	data = request.get_json()
	dom_content = data.get('dom')
	print(dom_content)
	return "test dom endpoint"

@app.route("/translate", methods=["POST"])
def translate():
	print("hit translate")
	data = request.get_json()
	print(data)

	response = client.models.generate_content(
		model='gemini-2.0-flash-001', 
		contents=data["text"],
		config=generation_config
	)
	
	print(response.text)
	translated_arr = json.loads(response.text)
	translated_obj = {"text": translated_arr}
	payload = json.dumps(translated_obj)
	print(payload)
	
	return payload

if __name__ == '__main__':
    app.run(debug=True)