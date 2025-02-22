function getTextNodes(node, textNodes = []) { //dfs traverse & keep text node references
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.textContent.trim() !== "") {
        textNodes.push({ node: node, text: node.textContent }); 
      }
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      for (const childNode of node.childNodes) {
        getTextNodes(childNode, textNodes);
      }
    }
    return textNodes;
  }
  
  
  function sendTextNodes(textNodes) {
    const textArray = textNodes.map((item) => item.text); //grab text only
  
    fetch("http://127.0.0.1:5000/translate", { //request
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text: textArray }),
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`http error status: ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        console.log("api res:", data);
        replaceTextNodes(textNodes, data.text); // call replacement function after res returned
      })
      .catch((error) => {
        console.error("error sending text nodes:", error);
      });
  }

  function replaceTextNodes(textNodes, replacementTexts) {
    if (!Array.isArray(replacementTexts)) {
      console.error("replacementTexts is not an array");
      return;
    }
    //temp check since I have been hard capping the LLM output length, need to add the relevant post-processing LLM res text
    if (textNodes.length !== replacementTexts.length) {
      console.error("text nodes and replacement texts array lengths do not match.");
      return;
    }
    for (let i = 0; i < replacementTexts.length; i++) {
      textNodes[i].node.textContent = replacementTexts[i];
    }
  }

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "translate") {
      const textNodes = getTextNodes(document.body);
      sendTextNodes(textNodes);
      sendResponse({ result: "text extracted and sending initiated" });
    }
  });