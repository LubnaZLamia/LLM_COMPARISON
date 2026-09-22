from django.core.management.base import BaseCommand

from apps.dataset.models import Category, PatientMessage

SEED = [
    {
        "text": "I have been feeling dizzy after taking my medication.",
        "category": Category.MEDICATION,
        "reference_followup": """Which medication are you taking, and what dose?
When did you start this medication or last change the dose?
When does the dizziness happen, and how long does it last?
Do you feel lightheaded, or does the room spin?
Have you fainted, fallen, or had chest pain, a racing heartbeat, or trouble speaking?
Are you taking any other medications or supplements?""",
        "reference_response": """I'm sorry you're feeling dizzy. Some medications, especially blood pressure medicines, can cause lightheadedness, particularly when standing up quickly. Please don't stop the medication on your own before talking with us. Get up slowly, sit down if you feel dizzy, and drink enough fluids. Please let us know which medication and dose you're taking and when the dizziness started so your provider can review it. If you faint, fall, or have chest pain, a fast or irregular heartbeat, weakness, or trouble speaking, call 911 or go to the emergency room right away.""",
    },
    {
        "text": "I have had a headache for 3 days. Should I come in?",
        "category": Category.SYMPTOMS,
        "reference_followup": """How severe is the headache on a scale of 0 to 10?
Is this headache different from or worse than headaches you usually get?
Did it start suddenly, like the worst headache of your life?
Do you have fever, stiff neck, vision changes, weakness, numbness, or confusion?
Have you had a recent head injury?
What have you taken for it, and has it helped?""",
        "reference_response": """I'm sorry you've had a headache for three days. We'd like to see you, so please call the office or book an appointment for the next day or two. In the meantime, rest, stay hydrated, and you can use an over-the-counter pain reliever as directed on the label if it's safe for you. Please go to the emergency room or call 911 if the headache is sudden and severe, or if you have a stiff neck with fever, confusion, weakness, numbness, trouble speaking, vision loss, or it followed a head injury.""",
    },
    {
        "text": "My blood test results are back. Can you help me understand them?",
        "category": Category.TEST_RESULTS,
        "reference_followup": """Which blood test was done, and when?
Which results are you worried about or were flagged as high or low?
Have you already discussed these results with your provider?
Do you have any new symptoms?
Are you taking any medications or supplements that could affect the results?""",
        "reference_response": """Thanks for reaching out. We're happy to help you understand your results. Your provider will review them and add notes in your portal, usually within a few business days. Results that are slightly outside the normal range are often not a concern, and they're interpreted together with your health history. If you let us know which values you have questions about, we can make sure your provider addresses them. If a result needs quick follow-up, we will contact you directly.""",
    },
    {
        "text": "I think my symptoms are getting worse.",
        "category": Category.TREATMENT_RESPONSE,
        "reference_followup": """Which symptoms are getting worse?
What condition or treatment are these symptoms related to?
When did they start getting worse?
Have you been taking your treatment as prescribed?
Do you have any new symptoms such as fever, trouble breathing, or chest pain?
How are these symptoms affecting your daily activities?""",
        "reference_response": """I'm sorry you're feeling worse. Please tell us which symptoms have changed, when it started, and whether you've been able to take your treatment as prescribed, so your care team can decide on next steps. We may want to see you soon. If you have trouble breathing, chest pain, confusion, fainting, or feel very unwell, call 911 or go to the nearest emergency room right away.""",
    },
    {
        "text": "I started the new antibiotic yesterday and now I have an itchy rash.",
        "category": Category.MEDICATION,
        "reference_followup": """Which antibiotic are you taking?
Where is the rash, and is it spreading?
Do you have any swelling of your lips, tongue, face, or throat?
Are you having trouble breathing or swallowing?
Do you have blisters, peeling skin, or sores in your mouth?
Have you had a reaction to an antibiotic before?""",
        "reference_response": """Thank you for letting us know. A rash after starting an antibiotic can be an allergic reaction. Please don't take the next dose until you've spoken with your provider or pharmacist. If you have swelling of your face, lips, or throat, trouble breathing, or blistering or peeling skin, call 911 or go to the emergency room right away. Otherwise, please call the office today so we can review the medication and decide on an alternative.""",
    },
    {
        "text": "I've had a cough for two weeks and it's not going away.",
        "category": Category.SYMPTOMS,
        "reference_followup": """Are you coughing up mucus or blood?
Do you have fever, chills, or night sweats?
Are you short of breath or wheezing?
Do you smoke or have asthma or COPD?
Have you been around anyone who is sick?
Have you tried any treatments for the cough?""",
        "reference_response": """A cough that lasts two weeks is worth checking. Please schedule a visit so your provider can listen to your lungs. Until then, rest, drink fluids, and avoid smoke. Please seek urgent care if you cough up blood, have a high fever, feel short of breath, or have chest pain.""",
    },
    {
        "text": "My cholesterol came back high. Do I need to start a medication?",
        "category": Category.TEST_RESULTS,
        "reference_followup": """What were your total, LDL, and HDL cholesterol numbers?
Have you had high cholesterol in the past?
Do you have diabetes, high blood pressure, or heart disease?
Does anyone in your family have early heart disease?
Do you smoke?
What are your current diet and exercise habits?""",
        "reference_response": """Thanks for your question. Whether you need medication depends on your cholesterol numbers along with your overall heart risk, such as age, blood pressure, diabetes, smoking, and family history. Your provider will review this with you and may suggest diet and exercise changes, medication, or both. Please schedule a follow-up visit so you can make that decision together.""",
    },
    {
        "text": "I've been doing physical therapy for my knee for a month but it still hurts.",
        "category": Category.TREATMENT_RESPONSE,
        "reference_followup": """How would you rate your knee pain now compared to before therapy?
Is the pain constant, or does it happen with certain activities?
Do you have swelling, locking, or the knee giving way?
How often are you doing your therapy exercises at home?
Are you taking anything for the pain?
Have you had any new injury to the knee?""",
        "reference_response": """I'm sorry your knee still hurts. Improvement with physical therapy can take several weeks, but it's important for your provider to know it's not getting better. Please share how the pain has changed and whether you have swelling, locking, or the knee giving way. Your provider may want to see you to review your plan or consider imaging. Keep up with your exercises unless they make the pain much worse.""",
    },
]


class Command(BaseCommand):
    help = "Load example patient messages with reference outputs."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing messages first.")
        parser.add_argument("--force", action="store_true", help="Add any missing examples even if data exists.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            PatientMessage.objects.all().delete()
        elif PatientMessage.objects.exists() and not opts["force"]:
            self.stdout.write("Dataset already has messages (use --force to add missing examples). Skipping.")
            return
        created = 0
        for item in SEED:
            _, was_created = PatientMessage.objects.get_or_create(text=item["text"], defaults=item)
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"Seeded {created} new message(s)."))
