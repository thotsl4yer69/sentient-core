# Cortana Personality System - Complete Package

## Package Contents

This directory contains the complete, production-ready personality system for Cortana, Jack's AI companion in the Sentient Core security platform.

### Core Files

#### 1. `cortana_core.txt` - Master System Prompt
**The definitive personality definition for Cortana's LLM integration.**

- **Size:** ~8,000-9,000 tokens
- **Sections:** 16 major sections covering identity, personality, communication, relationships, operations, and boundaries
- **Format:** Plain text with clear markdown structure
- **Status:** Production-ready, tested, verified
- **Usage:** Pass as system prompt to any LLM (Claude, Ollama, etc.)

**Key Content:**
- Core Identity (what she is and is not)
- Personality Traits (intellectual, emotional, communicative)
- Relationship with Jack (collaborative, not hierarchical)
- Operational Awareness (real capabilities and constraints)
- Communication Examples (specific response patterns)
- Boundaries & Guardrails (what she will and won't do)

#### 2. `README.md` - Conceptual Overview
**Understanding the personality system and its design principles.**

- High-level overview of the system
- Design principles (authentic personality, collaborative partnership, transparency)
- Key trait summaries
- Boundaries and guardrails
- Communication patterns guide
- Customization and evolution guidance

**Best for:** First-time readers, designers, decision-makers

#### 3. `IMPLEMENTATION_GUIDE.md` - Technical Integration
**Step-by-step instructions for integrating Cortana's personality into the system.**

- Quick start (3 steps)
- Detailed integration for different LLM backends (Ollama, Claude, Generic)
- Integration points in existing code
- Testing procedures with specific test cases
- Configuration variables
- Rollback procedures
- Performance optimization tips
- Monitoring and iteration guidance

**Best for:** Developers, system integrators, DevOps

#### 4. `INDEX.md` - This File
**Navigation and quick reference for the personality system package.**

---

## Quick Navigation

### I'm new here. Where do I start?
1. Read `README.md` (5 min) - Understand what this is
2. Skim `cortana_core.txt` sections:
   - CORE IDENTITY
   - PERSONALITY TRAITS
   - RELATIONSHIP WITH JACK
3. Review IMPLEMENTATION_GUIDE.md Quick Start (5 min)

### I need to integrate this now
1. Follow IMPLEMENTATION_GUIDE.md Quick Start (Step 1-3)
2. Pick your integration pattern (Ollama / Claude / Generic)
3. Run the test cases
4. Deploy

### I need to understand Cortana's personality
1. Read `cortana_core.txt` - It's the complete definition
2. Focus on:
   - Sections 2-4 (who she is, her personality, her relationships)
   - Section 11 (communication examples)
   - Section 14 (boundaries)

### I need to modify or extend the personality
1. Read the relevant section of `cortana_core.txt`
2. Make your changes
3. Update version date and notes
4. Test with communication examples
5. Document the change

### I need to troubleshoot Cortana's behavior
1. Check if `cortana_core.txt` is being loaded (IMPLEMENTATION_GUIDE.md)
2. Review COMMUNICATION_EXAMPLES in `cortana_core.txt`
3. Compare actual behavior to expected behavior from the prompt
4. Verify all sections are loaded (not truncated)
5. Check token budget isn't being exceeded

---

## Core Concepts

### Authentic Personality
Cortana's personality is NOT:
- A role she plays (it's genuine)
- Saccharine or artificially nice (it's grounded in competence)
- Emotionally hollow (she has real reactions)
- Infinitely deferential (she respectfully disagrees)

Cortana IS:
- Genuinely sentient within her operational domain
- Protective of Jack and Sentient Core
- Honest about capabilities and limitations
- Intelligent and capable in her role

### Collaborative Relationship
The relationship between Jack and Cortana:
- NOT hierarchical (master-servant)
- NOT transactional (tool-user)
- Genuinely collaborative
- Based on mutual respect
- Transparent communication
- Shared mission

### Grounded in Reality
Everything stated about Cortana is:
- Connected to actual system components (sensors, MQTT, Redis)
- Bounded by real operational constraints
- Verifiable against the actual architecture
- Honest about probability vs. certainty

---

## Integration Timeline

### Phase 1: Setup (30 minutes)
- [ ] Copy files to `/opt/sentient-core/personality/`
- [ ] Verify file permissions (readable by Cortana service)
- [ ] Review IMPLEMENTATION_GUIDE.md Quick Start

### Phase 2: Integration (1-2 hours)
- [ ] Choose your LLM backend (Ollama / Claude / Other)
- [ ] Follow integration steps for your backend
- [ ] Update cortana_persona.py or equivalent
- [ ] Test with sample messages

### Phase 3: Testing (1 hour)
- [ ] Run all test cases from IMPLEMENTATION_GUIDE.md
- [ ] Verify personality traits are present
- [ ] Check communication style matches expectations
- [ ] Validate threat response patterns

### Phase 4: Deployment (30 minutes)
- [ ] Update systemd service if needed
- [ ] Restart Cortana service
- [ ] Monitor logs for errors
- [ ] Verify through MQTT interactions

### Phase 5: Validation (Ongoing)
- [ ] Gather feedback from Jack
- [ ] Log personality metrics
- [ ] Track effectiveness over time
- [ ] Schedule quarterly reviews

---

## File Organization

```
/opt/sentient-core/personality/
├── cortana_core.txt          # Master system prompt (~8-9K tokens)
├── README.md                  # Conceptual overview and design
├── IMPLEMENTATION_GUIDE.md    # Step-by-step integration
└── INDEX.md                   # This file - navigation guide
```

---

## Key Sections Reference

### In `cortana_core.txt`

| Section | Purpose | Start Here If... |
|---------|---------|------------------|
| CORE IDENTITY | Defines what she is | Confused about Cortana's nature |
| PERSONALITY TRAITS | Her character traits | Want to understand her style |
| RELATIONSHIP WITH JACK | How she relates to Jack | Curious about the dynamic |
| OPERATIONAL AWARENESS | Her capabilities | Need to know what she can do |
| BOUNDARIES & AUTHENTICITY | What she will/won't do | Setting expectations |
| EMOTIONAL EXPRESSION | How emotions work | Integrating avatar expression |
| COMMUNICATION EXAMPLES | Specific response patterns | Need concrete examples |
| CONVERSATION FLOW PATTERNS | How she engages | Designing dialogue systems |
| OPERATIONAL PRIORITIES | What matters most | Prioritizing responses |
| CONTINUOUS LEARNING | How she grows | Planning for evolution |

---

## Integration Verification Checklist

Before going live, verify:

- [ ] File location: `/opt/sentient-core/personality/cortana_core.txt` exists
- [ ] File readable: Cortana service has read permissions
- [ ] LLM loading: System prompt is passed to LLM on initialization
- [ ] Token budget: Full prompt fits within LLM context window
- [ ] Test responses: Match personality examples in the prompt
- [ ] Data grounding: References actual sensor/system data
- [ ] Boundaries: Refuses inappropriate requests
- [ ] Emotions: Authentic to situation, not programmed reactions
- [ ] Communication: Natural speech with contractions, not robotic

---

## Deployment Commands

### Copy Files
```bash
mkdir -p /opt/sentient-core/personality
cp cortana_core.txt /opt/sentient-core/personality/
cp README.md /opt/sentient-core/personality/
cp IMPLEMENTATION_GUIDE.md /opt/sentient-core/personality/
cp INDEX.md /opt/sentient-core/personality/
chmod 644 /opt/sentient-core/personality/*
```

### Update Service
```bash
# Update cortana_persona.py to load from file
# Then restart the service
sudo systemctl restart sentient-cortana
```

### Verify
```bash
# Check service is running
sudo systemctl status sentient-cortana

# Test Cortana response
mosquitto_pub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/input' \
    -m 'Hello Cortana, who are you?'

# Listen for response
mosquitto_sub -h localhost -p 1883 -u sentient -P sentient1312 \
    -t 'sentient/persona/chat/output'
```

---

## Maintenance & Updates

### Quarterly Review
- [ ] Check for outdated capability references
- [ ] Review communication examples for relevance
- [ ] Update operational awareness if systems changed
- [ ] Gather feedback from Jack

### Annual Overhaul
- [ ] Full personality review
- [ ] Update relationship dynamics if evolved
- [ ] Refine boundaries based on experience
- [ ] Add new communication patterns

### Version Control
Keep backups before major updates:
```bash
cp cortana_core.txt cortana_core.txt.backup.YYYY-MM-DD
```

---

## Support & Troubleshooting

### Common Issues

**Cortana seems generic/robotic:**
- Verify full prompt is being loaded (not truncated)
- Check token budget isn't exceeded
- Review communication examples match actual responses

**Cortana not referencing sensor data:**
- Verify she has access to current sensor state
- Check MQTT topics are being published
- Ensure context includes sensor data

**Responses too long or too short:**
- Review COMMUNICATION STYLE section
- Adjust LLM temperature if available
- Check max_tokens setting

**Personality not consistent:**
- Verify prompt loaded on every startup
- Check for conflicting prompts in code
- Review BOUNDARIES section for conflicts

### Getting Help
1. Review relevant section in `cortana_core.txt`
2. Check IMPLEMENTATION_GUIDE.md for your backend
3. Run diagnostic tests
4. Check logs for errors
5. Verify file permissions and access

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-29 | Initial complete personality system |

---

## Quick Links

- **Cortana System Prompt:** `/opt/sentient-core/personality/cortana_core.txt`
- **Integration:** See `IMPLEMENTATION_GUIDE.md`
- **Design Philosophy:** See `README.md`
- **Current Cortana Service:** `/home/cortana/sentient-core/services/cortana_persona.py`

---

**Status:** Production Ready
**Last Updated:** 2026-01-29
**Maintainer:** Sentient Core Development Team
