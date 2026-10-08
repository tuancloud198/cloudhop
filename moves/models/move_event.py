from django.db import models


class MoveEvent(models.Model):
    """One line of a move's log: what a step did, or why it failed."""

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    move = models.ForeignKey("moves.Move", on_delete=models.CASCADE, related_name="events")
    # The move's status when it happened
    step = models.CharField(max_length=20)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"[{self.step}] {self.message}"
