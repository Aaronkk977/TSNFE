"""Transcription engine factory and exports."""


class TranscriberFactory:
	"""Factory for selecting transcription provider."""

	@staticmethod
	def create(settings, pipeline_config=None):
		if pipeline_config is None:
			from ..utils.config import get_pipeline_config

			pipeline_config = get_pipeline_config()

		provider = pipeline_config.get_transcription_provider(default="gemini")
		if provider not in {"gemini", "whisper"}:
			provider = (settings.transcription_provider or "gemini").lower()

		print(f"[TranscriberFactory] Using provider: {provider}")
		if provider == "whisper":
			from .whisper_engine import WhisperTranscriber
			return WhisperTranscriber(settings, pipeline_config)
		from .gemini_engine import GeminiTranscriber
		return GeminiTranscriber(settings, pipeline_config)


__all__ = [
	"TranscriberFactory",
]
