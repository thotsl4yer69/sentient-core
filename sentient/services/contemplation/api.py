#!/usr/bin/env python3
"""
Contemplation HTTP API - FastAPI Wrapper for ContemplationEngine

Exposes the multi-voice contemplative reasoning engine via HTTP endpoints.
Provides RESTful access to Cortana's internal dialogue system.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

from sentient.config import get_config
from sentient.common.logging import setup_logging
from .engine import (
    ContemplationEngine,
    ContemplationResult,
    InputType,
    EmotionState,
    ExpressionHints
)

# Logger
logger = setup_logging("contemplation_http")


# Request/Response Models
class GenerateRequest(BaseModel):
    """Request model for /generate endpoint"""
    input: str = Field(..., description="Text input to contemplate")
    user_id: Optional[str] = Field(None, description="User identifier for context")
    memories: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Recent conversation context")
    world_state: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Current world state")
    conversation_context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Conversation metadata")
    history: Optional[List[Dict[str, str]]] = Field(default_factory=list, description="Recent conversation turns [{role, content}]")
    system_context: Optional[str] = Field(None, description="Additional context for system prompt (core facts, mood, tool output)")


class VoiceResponse(BaseModel):
    """Individual voice perspective in response"""
    content: str
    generation_time_ms: float


class EmotionResponse(BaseModel):
    """Emotion state in response"""
    primary: str
    valence: float
    arousal: float
    intensity: float
    confidence: float


class ExpressionResponse(BaseModel):
    """Expression hints in response"""
    gestures: List[str]
    tone: str
    pacing: str


class ThinkingProcessResponse(BaseModel):
    """Complete thinking process breakdown"""
    voices: Dict[str, VoiceResponse]
    total_time_ms: float


class GenerateResponse(BaseModel):
    """Response model for /generate endpoint"""
    response: str = Field(..., description="Synthesized response text")
    emotion: EmotionResponse = Field(..., description="Detected emotional state")
    expression: ExpressionResponse = Field(..., description="Physical expression cues")
    thinking_process: ThinkingProcessResponse = Field(..., description="Internal voice perspectives")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: str
    ollama_host: str
    contemplation_count: int


# Global engine instance
engine: Optional[ContemplationEngine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown"""
    global engine

    # Startup: Initialize ContemplationEngine
    logger.info("Starting Contemplation HTTP API...")

    engine = ContemplationEngine()
    await engine.start()

    logger.info("Contemplation HTTP API ready")

    yield

    # Shutdown: Clean up engine
    logger.info("Stopping Contemplation HTTP API...")
    if engine:
        await engine.stop()
    logger.info("Contemplation HTTP API stopped")


# FastAPI application
app = FastAPI(
    title="Contemplation HTTP API",
    description="HTTP wrapper for Cortana's multi-voice contemplative reasoning engine",
    version="2.0.0",
    lifespan=lifespan
)


# CORS middleware - allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint

    Returns service status and basic metrics.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    return HealthResponse(
        status="healthy",
        timestamp=datetime.now().isoformat(),
        ollama_host=engine.ollama_host,
        contemplation_count=engine._contemplation_count
    )


@app.post("/generate", response_model=GenerateResponse)
async def generate_response(request: GenerateRequest):
    """
    Generate contemplative response

    Processes input through the multi-voice contemplation engine and returns:
    - Synthesized natural language response
    - Detected emotional state
    - Physical expression hints
    - Complete thinking process (all voice perspectives)

    The engine uses five internal voices:
    - OBSERVER: Raw factual observations
    - ANALYST: Logical implications
    - EMPATH: Emotional resonance
    - SKEPTIC: Alternative interpretations
    - MEMORY: Past context connections
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    try:
        # Add conversation context to memory if provided
        if request.memories:
            for memory in request.memories:
                interaction = memory.get('interaction', {})
                user_msg = interaction.get('user_msg', '')
                assistant_msg = interaction.get('assistant_msg', '')
                similarity = memory.get('similarity', 0)
                if user_msg and assistant_msg:
                    # Format as a natural memory recall
                    memory_text = f"Jack said: '{user_msg[:100]}' and I replied: '{assistant_msg[:100]}'"
                    engine.memory.add_memory(memory_text, category="recalled")
                elif user_msg:
                    engine.memory.add_memory(f"Jack once said: '{user_msg[:150]}'", category="recalled")

        # Perform contemplation
        result: ContemplationResult = await engine.contemplate(
            input_text=request.input,
            input_type=InputType.TEXT,
            conversation_history=request.history or []
        )

        # Convert voices to response format
        voices_dict = {
            voice_name: VoiceResponse(
                content=perspective.content,
                generation_time_ms=perspective.generation_time_ms
            )
            for voice_name, perspective in result.perspectives.items()
        }

        # Build response
        response = GenerateResponse(
            response=result.synthesized_response,
            emotion=EmotionResponse(
                primary=result.emotion.primary.value,
                valence=result.emotion.valence,
                arousal=result.emotion.arousal,
                intensity=result.emotion.intensity,
                confidence=result.emotion.confidence
            ),
            expression=ExpressionResponse(
                gestures=result.expression.gestures,
                tone=result.expression.tone,
                pacing=result.expression.pacing
            ),
            thinking_process=ThinkingProcessResponse(
                voices=voices_dict,
                total_time_ms=result.total_time_ms
            )
        )

        logger.info(
            f"Generated response for '{request.input[:30]}...' in {result.total_time_ms:.0f}ms "
            f"(emotion: {result.emotion.primary.value})"
        )

        return response

    except Exception as e:
        logger.error(f"Error generating response: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate response: {str(e)}")


@app.post("/generate/stream")
async def generate_stream(request: GenerateRequest):
    """
    Stream contemplative response as Server-Sent Events (SSE).

    Returns chunked tokens as they are generated by the LLM,
    enabling progressive display with ~100ms per-token latency
    instead of waiting 30-180s for a full response.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    async def event_generator():
        try:
            # Recalled memories are now passed via system_context from the orchestrator
            # No longer injected into engine's local MemoryStore to prevent context pollution

            yield f"data: {json.dumps({'thinking': True, 'done': False})}\n\n"

            token_count = 0
            async for token in engine.contemplate_stream(
                input_text=request.input,
                input_type=InputType.TEXT,
                conversation_history=request.history or [],
                system_context=request.system_context
            ):
                if token_count == 0:
                    yield f"data: {json.dumps({'thinking': False, 'done': False})}\n\n"
                token_count += 1
                yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"

            yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"

        except Exception as e:
            logger.error(f"Error in streaming response: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Contemplation HTTP API",
        "version": "2.0.0",
        "description": "HTTP wrapper for Cortana's multi-voice contemplative reasoning engine",
        "endpoints": {
            "/health": "GET - Health check",
            "/generate": "POST - Generate contemplative response",
            "/generate/stream": "POST - Stream contemplative response (SSE)",
            "/docs": "GET - Interactive API documentation"
        }
    }


def main():
    """Entry point for running the server"""
    cfg = get_config()
    port = cfg.contemplation.port

    uvicorn.run(
        "sentient.services.contemplation.api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
