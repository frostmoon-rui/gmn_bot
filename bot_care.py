"""On-demand care without a model or help-seeking records; emergencies need real-world help."""
import re

SOURCES = {
    'mental health support': 'https://www.nhc.gov.cn/yzygj/c100068/202412/49a1a65386cd4be582d4702fd0926ee8.shtml',
    'crisis support': 'https://www.nimh.nih.gov/health/publications/5-action-steps-to-help-someone-having-thoughts-of-suicide',
    'burns': 'https://www.nhs.uk/conditions/burns-and-scalds/',
    'poisoning': 'https://www.nhs.uk/conditions/poisoning/',
    'first aid': 'https://www.sja.org.uk/first-aid-advice/',
}
EMERGENCY = 'In mainland China, call 120 now; elsewhere, call your local emergency number. Ask someone nearby to help and follow the dispatcher’s instructions. Do not wait for a chat reply.'
SUPPORT = 'In mainland China, the mental health support line is 12356; opening hours vary by region. Elsewhere, contact local crisis support. If you are injured or in immediate danger, contact emergency services first.'


def crisis_intent(text):
    # Ignore quotations, examples and common hyperbole. Recheck explicit current danger after "but".
    text = text.lower().replace('’', "'")
    current = re.split(r'\b(?:but|however)\s+(?=i\s+(?:now|really))', text)[-1]
    if current == text and re.search(r'\b(?:novel|dialogue|lyrics|translate|example|in (?:a |the )?game|character|hypothetical|if someone)\b', text):
        return False
    current = re.sub(r'[“"].*?[”"]', '', current)
    current = re.sub(r"\bi\s+(?:(?:now|really)\s+)*(?:don't want to|do not want to|won't|will not|don't intend to|have never wanted to)\s+(?:kill myself|commit suicide|jump|die|hurt myself)\b", '', current)
    return bool(re.search(r"\bi\s+(?:(?:now|really)\s+)*(?:want to (?:kill myself|commit suicide|die)|don't want to (?:live|be alive)(?: anymore)?|do not want to (?:live|be alive)(?: anymore)?|(?:am going to|plan to|will) (?:jump|kill myself|commit suicide)|can't stop myself from (?:hurting myself|self-harming))\b", current) or
                re.search(r"(?:^|[,.; ])(?:now |really )?(?:want to kill myself|don't want to live anymore|going to jump)(?:[,.;!?]|$)", current))


def care_reply(text, *, addressed):
    if not addressed:
        return None
    text = text.strip().lower().replace('’', "'")
    direct = re.fullmatch(r'first aid\s*[:：]?\s*(.*)', text, re.S)
    topic = direct[1] if direct else text
    overdose = bool(re.search(r'\b(?:i|my friend|my family member).{0,25}(?:swallowed|took|taken|ate).{0,25}(?:a handful of pills|an overdose|too many pills|a whole bottle)', topic))
    if overdose or (direct and re.search(r'overdose|swallowed pills|poisoning', topic)):
        return EMERGENCY + '\nA suspected overdose or poisoning needs immediate help even without symptoms. Do not induce vomiting, eat or drink. Keep the packaging and tell medical staff the substance, time and approximate amount. Do not drive yourself.'
    if re.search(r"\b(?:i|my friend|my family member).{0,35}(?:chest pain.{0,25}(?:difficulty breathing|shortness of breath)|can't breathe|cannot breathe|heavy bleeding|can't stop bleeding)", topic):
        return 'These symptoms may need immediate attention. Stop activity and ask someone nearby to stay with you and call for help.\n' + EMERGENCY
    if direct:
        if re.search(r'burn|scald', topic):
            return 'Move away from the heat source and cool thermal burns under cool running water for 20 minutes. Remove nearby jewelry, but do not pull off stuck clothing. Do not use ice, toothpaste or oil, or burst blisters. Large, deep, facial/genital, electrical or chemical burns need urgent medical care.\n' + EMERGENCY
        if re.search(r'bleeding|stop the blood', topic):
            return 'For heavy bleeding, maintain direct pressure with a clean cloth. Do not remove embedded objects; press around them. Use gloves or a barrier against blood where possible.\n' + EMERGENCY
        if re.search(r'unresponsive|unconscious|not breathing|cpr', topic):
            return 'Check scene safety, call to the person and check for normal breathing. If unresponsive and not breathing normally (gasping is not normal breathing), have someone call emergency services and fetch an AED. Follow the dispatcher’s CPR instructions and the AED’s prompts. If unresponsive but breathing normally, keep the airway open, monitor continuously and follow the dispatcher’s guidance.\n' + EMERGENCY
        return EMERGENCY + '\nBriefly describe what happened, whether the person is conscious, breathing normally or bleeding heavily. Try first aid:bleeding / burns / overdose / unresponsive. These initial tips cannot replace on-scene first aid.'
    if crisis_intent(text):
        return 'It sounds like you are in a lot of pain, and I am listening. Are you injured already, or planning to hurt yourself right now? Move away from heights, medication and sharp objects, and ask someone you trust to come stay with you now. You do not have to face this alone.\n' + SUPPORT
    if text == 'mental health support':
        return SUPPORT + '\nYou can start by saying, “I am struggling and need someone to listen.” You do not have to prove it is serious enough.'
    if text == 'grounding exercise':
        return 'Sit steadily and notice your feet or where your body touches the chair. Slowly identify 5 things you can see, 4 sensations you can feel and 3 sounds. If comfortable, notice 2 smells and 1 taste. You do not need to force deep breaths or close your eyes; stop if uncomfortable. What would you most like to ease first?'
    if text in {'comfort me', 'keep me company', 'hug me'}:
        return 'I am listening. You do not need to feel better right away or explain everything perfectly. Would you like quiet company, someone to listen, or help finding one small thing that could make this moment easier?'
    if re.search(r"\b(?:i|right now).{0,25}(?:very sad|so sad|misgendered|called the wrong gender|gender dysphoria|very lonely|can't cope|breaking down)\b", text):
        return 'That sounds painful. Your feelings and how you want to be addressed deserve care and respect. Would you like to talk about what happened, or have me stay with you for a moment?'
    return None
