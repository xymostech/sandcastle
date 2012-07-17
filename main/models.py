from django.db import models

class PhabricatorReview(models.Model):
    review_id = models.CharField(max_length=30)
    exercise_related = models.BooleanField()
