"""
Microsoft Teams meeting scheduler — Graph API integration.

Public exports:
  schedule_teams_meeting  — create a meeting + optional HR calendar event
  cancel_teams_meeting    — delete a meeting (used on resend)
"""
from .scheduler import schedule_teams_meeting, cancel_teams_meeting

__all__ = ["schedule_teams_meeting", "cancel_teams_meeting"]