# WebView Context Switching

Many Android apps embed web content via WebView. When a WebView is active, the accessibility tree shows the native container but not the web DOM inside it. You need to switch contexts.

## Detecting WebViews

```python
# Check for WebView elements in the native tree
wv = find(type="android.webkit.WebView")
if wv:
    print("WebView found at", wv["cx"], wv["cy"])
```

## Switching contexts

```python
# List available contexts
contexts = appium("mobile: getContexts")
# Typically: ['NATIVE_APP', 'WEBVIEW_com.example.app']

# Switch to webview
appium("mobile: setContext", name="WEBVIEW_com.example.app")
# Now find() returns web DOM elements (CSS selectors work)

# Switch back to native
appium("mobile: setContext", name="NATIVE_APP")
```

## Working in WebView context

Once in webview context, element finding changes:

```python
# Native context: UIAutomator selectors
find(text="Login")  # → android.widget.TextView

# WebView context: web elements
# Use click() with XPath or CSS
click("//button[text()='Login']", by="xpath")
click("#login-btn", by="id")  # CSS id
```

## Gotchas

- **Chrome DevTools must be enabled**: the app must set `WebView.setWebContentsDebuggingEnabled(true)`. Without it, the webview context won't appear. Production apps often disable this.
- **Multiple WebViews**: if the app has multiple, each gets its own context name. Match by package.
- **Hybrid apps** (Ionic, Cordova, React Native WebView): almost everything is in a WebView. Switch to webview context for most interactions.
- **iframes**: WebView context sees iframes as separate documents. May need to switch frames within the webview.
- **Back to native**: always `setContext("NATIVE_APP")` when done — otherwise `ui_tree()` returns web elements.
- **Fallback**: if context switching fails, use OCR on screenshots — works regardless of context.
