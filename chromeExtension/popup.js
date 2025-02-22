// popup.js
document.getElementById('translate').addEventListener('click', () => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      chrome.scripting.executeScript({
        target: { tabId: tabs[0].id },
        files: ['content.js']
      }, () => {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'translate' }, (response) => {
          if (chrome.runtime.lastError) {
            document.getElementById('status').textContent = "Error: " + chrome.runtime.lastError.message;
            console.error(chrome.runtime.lastError);
          } else {
            document.getElementById('status').textContent = response.result;
            console.log('Content script response:', response);
          }
        });
      });
    });
  });