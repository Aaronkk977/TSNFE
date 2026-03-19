"""Transcription engine factory and exports."""

from .gemini_engine import GeminiTranscriber
from .whisper_engine import WhisperTranscriber


class TranscriberFactory:
	"""Factory for selecting transcription provider."""

	@staticmethod
	def create(settings):
		provider = (settings.transcription_provider or "gemini").lower()
		if provider == "whisper":
			return WhisperTranscriber(settings)
		return GeminiTranscriber(settings)


__all__ = [
	"GeminiTranscriber",
	"WhisperTranscriber",
	"TranscriberFactory",
]
