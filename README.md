## Setup
### Backend setup

setup & start your venv of choice (add to .gitignore pls)

cd into /backend

pip install -r 'requirements.txt'

rename 'exampleenv' to '.env' and replace 'yourapikey' with your api key lol

Start server:
python app.py (set to run in debug mode to restart on file save, use 'flask run' if you dont want that, but also we'll prob switch off flask if we continue anyways)


### Extension setup
go to 'chrome://extensions' in chrome
![image](https://github.com/user-attachments/assets/8b5f9745-572f-4412-9f81-a5536e3586f5)

click 'load unpacked' and select the chromeExtension folder

click the lil reload icon on the extension card when if you want to push local changes to browser.

to use just pin it, and click on the extension to reveal the button--had it act on DOM load at first but that was kinda annoying while testing

test it on http://localhost:5000/ first to make sure everything connected and API key is working


----------------------------------------------------------------------------------------------------------
seems to mostly only work on static pages rn but I need to do some more testing to figure out what things are breaking it. 
Right now it's sorta simulating worst-worst case scenario where all the textNodes on the page are replaced
the latency feels around 100-150ms for base infra + gemini api response time so obv this thing is slow af on most pages since it just grabs and translates everything

The textNodes come in as an array and I'm just passing in the full array as text with the prompt 
    "while maintaining the exact position of each element in the array, translate the plaintext items to chinese"

extension is traversing the full dom and replacing rn

priority:
  - determine best way to grab core content (minimize/optmize the token count)

other things:
  - move api calls/prompts to folder outside of app.py routes file LOL
  - add timer on api call and token count
  - experiment with other LLM apis
  - test LLM streaming instead of response
  - experiment with model settings (specifically want to test top P top K) have suspicion it could affect speed notably

If we can get it reasonably fast and the quality is good we should:
  - choose better backend (firebase looks convenient for the short/medium term but i've never used)
  - front end features

