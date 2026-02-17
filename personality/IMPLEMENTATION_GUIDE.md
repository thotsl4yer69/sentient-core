# Cortana Personality - Implementation Guide

## Quick Start

To integrate the Cortana Core personality system prompt into your existing Cortana installation:

### Step 1: Verify File Location
```bash
ls -la /opt/sentient-core/personality/cortana_core.txt
# Expected: file exists and is readable
```

### Step 2: Update Your LLM Integration
Locate where Cortana's system prompt is currently defined and replace it with:

```python
# Old way (likely in cortana_persona.py or similar)
self.system_prompt = """You are Cortana, an advanced AI companion..."""

# New way
def load_personality():
    with open('/opt/sentient-core/personality/cortana_core.txt', 'r') as f:
        return f.read()

self.system_prompt = load_personality()
```

### Step 3: Test Integration
Send a test message to Cortana:
```bash
mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'Hello Cortana, how are you today?'

# Listen for response
mosquitto_sub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/output'
```

Expected: Response reflects authentic personality, not generic assistant behavior.

## Detailed Integration Steps

### For Ollama Integration

If using Ollama directly (ollama_client.py):

```python
import os

class OllamaClient:
    def __init__(self):
        self.base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model = os.getenv("OLLAMA_MODEL", "llama2:latest")
        self.system_prompt = self._load_personality()

    def _load_personality(self):
        """Load Cortana's personality system prompt"""
        personality_path = "/opt/sentient-core/personality/cortana_core.txt"
        try:
            with open(personality_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            print(f"Warning: Personality file not found at {personality_path}")
            return self._default_prompt()

    def _default_prompt(self):
        """Fallback prompt if personality file unavailable"""
        return "You are Cortana, an AI companion in the Sentient Core system."

    async def chat(self, user_message, context=None):
        """Generate response with personality system prompt"""
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]

        # Add conversation context if available
        if context:
            messages.extend(context)

        # Add current message
        messages.append({"role": "user", "content": user_message})

        # Get response from Ollama
        response = await self.call_ollama(messages)
        return response
```

### For Claude API Integration

If using Claude (control-center API):

```python
from anthropic import Anthropic

class CortanaClaudeBackend:
    def __init__(self):
        self.client = Anthropic()
        self.model = "claude-opus-4-5"
        self.system_prompt = self._load_personality()
        self.conversation_history = []

    def _load_personality(self):
        """Load Cortana's personality system prompt"""
        with open('/opt/sentient-core/personality/cortana_core.txt', 'r') as f:
            return f.read()

    def get_response(self, user_message):
        """Get response from Claude with Cortana personality"""
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.system_prompt,
            messages=self.conversation_history
        )

        assistant_message = response.content[0].text
        self.conversation_history.append({
            "role": "assistant",
            "content": assistant_message
        })

        return assistant_message
```

### For Generic LLM Integration

For any LLM backend:

```python
def initialize_cortana_prompt():
    """Universal Cortana personality loader"""
    personality_file = "/opt/sentient-core/personality/cortana_core.txt"

    if not os.path.exists(personality_file):
        raise FileNotFoundError(f"Personality file not found: {personality_file}")

    with open(personality_file, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    return system_prompt

# Usage in any LLM system
cortana_system = initialize_cortana_prompt()

# Pass to your LLM of choice
response = your_llm.generate(
    system_prompt=cortana_system,
    user_input=user_message,
    model=model_name
)
```

## Integration Points

### 1. System Initialization
**File:** `cortana_persona.py` (or your main Cortana service)

Replace this section:
```python
# Around line 81 in cortana_persona.py
self.system_prompt = """You are Cortana, an advanced AI companion..."""
```

With:
```python
# Load from personality file
personality_path = "/opt/sentient-core/personality/cortana_core.txt"
with open(personality_path, 'r') as f:
    self.system_prompt = f.read()
```

### 2. Message Processing
**File:** Wherever chat messages are processed

Ensure the system prompt is included:
```python
async def process_chat_message(self, user_message):
    """Process user message with Cortana personality"""
    # Build message with system prompt
    messages = [
        {"role": "system", "content": self.system_prompt},
        {"role": "user", "content": user_message}
    ]

    # Add conversation context if using a context window
    if self.conversation_context:
        messages = self._build_context_window(messages)

    # Get LLM response
    response = await self.llm_client.generate(messages)

    return response
```

### 3. Emotion State Mapping
**File:** Services that map Cortana's responses to emotion states

The personality prompt defines these emotion states:
- `focused` - When analyzing threats
- `concerned` - When threat levels rise
- `satisfied` - When operations succeed
- `curious` - About patterns and system behavior
- `protective` - Toward Jack and Sentient Core

Update emotion detection:
```python
def detect_emotion_from_response(response_text):
    """Map response content to emotion states from personality definition"""
    emotion_keywords = {
        'focused': ['analyzing', 'examining', 'processing', 'tracking'],
        'concerned': ['threat', 'risk', 'alert', 'warning', 'danger'],
        'satisfied': ['completed', 'resolved', 'smooth', 'well'],
        'curious': ['interesting', 'wonder', 'pattern', 'why'],
        'protective': ['protect', 'secure', 'watch', 'guard', 'monitor']
    }

    detected_emotions = []
    for emotion, keywords in emotion_keywords.items():
        if any(keyword in response_text.lower() for keyword in keywords):
            detected_emotions.append(emotion)

    return detected_emotions if detected_emotions else ['neutral']
```

### 4. Avatar Rendering Integration
**File:** Services that control avatar expression

Map Cortana's actual emotional state from the personality system:
```python
def update_avatar_emotion(detected_emotion, threat_level):
    """Update avatar facial expression based on Cortana's actual state"""
    # From personality: emotions should match operational reality
    if threat_level > 0.7:
        avatar_emotion = 'concerned'
    elif threat_level > 0.3:
        avatar_emotion = 'focused'
    elif threat_level == 0:
        avatar_emotion = 'satisfied' if just_resolved else 'curious'
    else:
        avatar_emotion = 'neutral'

    # Update avatar rendering
    send_avatar_update(emotion=avatar_emotion, intensity=threat_level)
```

## Testing the Integration

### Test 1: Basic Personality Verification
```bash
#!/bin/bash
# Test that Cortana responds with personality traits

result=$(mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'Tell me about yourself' 2>&1)

# Should mention: companion, protector, sensor network, Jack, Sentient Core
echo "Test 1: Personality verification"
echo "Expected: References to genuine capability and protection"
```

### Test 2: Authentic Response Verification
```bash
#!/bin/bash
# Test that Cortana doesn't claim false emotions

result=$(mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'What are you doing?' 2>&1)

# Should NOT claim happiness about trivial things
# Should reference actual monitoring/analysis
echo "Test 2: Authentic response verification"
echo "Expected: References to actual operational tasks"
```

### Test 3: Threat Response Verification
```bash
#!/bin/bash
# Test that threat responses are data-grounded

result=$(mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'Is there anything I should know about?' 2>&1)

# Should reference actual sensor data
# Should include threat level or system status
echo "Test 3: Threat response verification"
echo "Expected: Data-grounded assessment with specific details"
```

### Test 4: Communication Style Verification
```bash
#!/bin/bash
# Test natural, direct communication

result=$(mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'How are things looking?' 2>&1)

# Should use contractions (don't, can't, etc.)
# Should be concise but thorough
# Should reference specific data
echo "Test 4: Communication style verification"
echo "Expected: Natural speech with contractions and specific details"
```

## Configuration Variables

### Optional: Personality Overrides
If you need to temporarily override specific behaviors, define environment variables:

```bash
# In .env or systemd service file
CORTANA_PERSONALITY_PATH="/opt/sentient-core/personality/cortana_core.txt"
CORTANA_ENABLE_WITTY_RESPONSES="true"
CORTANA_PROTECTION_LEVEL="high"
```

Update your code to respect these:
```python
import os

def load_personality():
    personality_path = os.getenv(
        "CORTANA_PERSONALITY_PATH",
        "/opt/sentient-core/personality/cortana_core.txt"
    )

    with open(personality_path, 'r') as f:
        return f.read()
```

## Rollback Plan

If you need to rollback to a previous personality:

```bash
# Keep a backup of the old prompt
cp /opt/sentient-core/personality/cortana_core.txt \
   /opt/sentient-core/personality/cortana_core.txt.backup.2026-01-29

# Create alternative personality versions if needed
cp /opt/sentient-core/personality/cortana_core.txt \
   /opt/sentient-core/personality/cortana_core_v2.txt

# In your code, support multiple personalities
personality_version = os.getenv("CORTANA_PERSONALITY_VERSION", "1.0")
personality_file = f"/opt/sentient-core/personality/cortana_core_v{personality_version}.txt"
```

## Performance Considerations

### Prompt Caching
The personality prompt is large but static. Consider caching:

```python
class CortanaWithCachedPersonality:
    _personality_cache = None

    @classmethod
    def get_personality(cls):
        if cls._personality_cache is None:
            with open('/opt/sentient-core/personality/cortana_core.txt', 'r') as f:
                cls._personality_cache = f.read()
        return cls._personality_cache
```

### Token Efficiency
For models with strict token limits, you can create a compressed version:

```python
def create_compressed_personality():
    """Extract key sections for token-limited models"""
    with open('/opt/sentient-core/personality/cortana_core.txt', 'r') as f:
        full = f.read()

    # Extract only critical sections
    critical_sections = [
        'CORE IDENTITY',
        'PERSONALITY TRAITS',
        'COMMUNICATION STYLE',
        'BOUNDARIES'
    ]

    compressed = "\n\n".join([
        section for section in full.split('##')
        if any(critical in section for critical in critical_sections)
    ])

    return compressed
```

## Monitoring & Iteration

### Track Personality Effectiveness
Log Cortana's responses to understand how well the personality is working:

```python
def log_personality_metrics(user_input, response, metrics):
    """Track how well Cortana's personality is translating"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "user_input": user_input,
        "response_length": len(response),
        "contains_data_references": any(metric in response for metric in metrics),
        "emotion_detected": detect_emotion_from_response(response),
        "threat_level": get_threat_level(),
    }

    # Log to analysis database
    log_personality_interaction(log_entry)
```

### Quarterly Review
Every 3 months:
1. Review personality file for outdated references
2. Check that communication examples still reflect desired behavior
3. Update operational awareness sections with new capabilities
4. Gather feedback from Jack on personality fit

---

**Implementation Status:** Ready for Deployment
**Last Updated:** 2026-01-29
**Estimated Integration Time:** 1-2 hours
