# XECO Thailand LINE Chatbot Pilot Instruction

## Purpose

This pilot is designed to test the XECO Thailand customer service chatbot on LINE for common EV charging support questions.

The purpose of the pilot is to validate:

- answer quality
- language handling
- screenshot-based support flow
- FAQ coverage
- operational stability

## Pilot Scope

During the pilot, the chatbot supports common customer questions related to:

- account and app usage
- charging start and stop process
- charging cable issues
- charging speed and charging behavior
- payment and refund questions
- general charging support and safety guidance

The chatbot currently supports:

- English
- Traditional Chinese
- Thai

The chatbot is designed to reply in the same language as the customer input:

- English input -> English reply
- Chinese input -> Traditional Chinese reply
- Chinese + English mixed input -> Traditional Chinese reply
- Thai input -> Thai reply

## What Customers Can Send

During the pilot, customers may send:

- text messages
- screenshots or photos related to charging issues

The screenshot flow is intended for cases such as:

- charger error screen
- app error screen
- payment confirmation or transaction proof
- charging status screen

Audio support is under technical validation and should not be treated as the primary pilot channel yet.

## How the Pilot Works

1. The customer sends a message to the XECO Thailand LINE Official Account.
2. If the customer sends text, the chatbot checks the approved XECO FAQ knowledge base.
3. If the customer sends a screenshot, the chatbot saves the file and attempts to understand the visible error or charging information.
4. The chatbot returns the most relevant answer in the detected language.
5. Conversation logs are stored for pilot review and quality improvement.

## What the Chatbot Can Do

The chatbot can currently:

- answer approved FAQ-based questions
- respond in multiple languages
- review screenshot-based charging errors
- keep a conversation log for review
- support common EV charging scenarios through LINE

## Customer Testing Guidance

During the pilot, please test questions such as:

- How do I start charging?
- I scanned the QR code but charging did not start.
- Why can’t I unplug the charging cable?
- I forgot my password.
- Can I use a landline phone number for registration?
- 我忘記密碼點算？
- 掃 Code 但充唔到電
- สแกน QR แล้วชาร์จไม่เริ่ม

You may also test by sending a screenshot of:

- charger error screen
- charging interruption screen
- app error message

## Recommended Customer Usage

For the best result during the pilot:

1. Send a short text question first whenever possible.
2. If there is an error on screen, send a screenshot.
3. If needed, add a short text explanation after the screenshot.

Example:

- screenshot of charger error
- text: `Pls advise the solution`

## Pilot Objectives

The main objectives of this pilot are to:

- confirm that customers can get useful answers through LINE
- confirm that the chatbot responds correctly in English, Chinese, and Thai
- confirm that screenshot-based support works for common charger error cases
- identify missing FAQ content
- identify incorrect or unclear answers
- confirm that the webhook and reply flow remain stable in live usage

## Current Limitations

This pilot version has some expected limitations:

- answers are based mainly on the FAQ knowledge base and simple retrieval logic
- some unusual or complex cases may still receive incomplete or generic answers
- screenshot understanding is available, but not all screenshots will be interpreted perfectly
- audio is not yet the main supported pilot channel
- the chatbot does not yet connect to live backend systems such as station status, wallet balance, refund processing, or ticketing
- FAQ updates require a content update and system redeployment or restart

## Pilot Review Process

During the pilot period:

- conversation logs will be reviewed
- weak or incorrect answers will be collected
- screenshot-based cases will be checked for quality
- FAQ coverage gaps will be identified
- improvements will be applied to the FAQ knowledge base and retrieval logic

## What To Report During The Pilot

Please note any case where:

- the answer is incorrect
- the answer is unclear
- the language is incorrect
- the chatbot does not reply
- the chatbot misunderstands a screenshot
- an important FAQ is missing

When reporting an issue, please include:

- the original customer message
- the chatbot reply
- screenshot if available
- date and time of the conversation

## Success Criteria

The pilot will be considered successful if:

- customers receive replies consistently through LINE
- the chatbot replies in the correct language
- common FAQ questions are answered correctly
- common screenshot-based charging errors receive practical guidance
- major failure cases are limited and identifiable
- the FAQ knowledge base can be improved efficiently based on pilot feedback

## Contact and Escalation

If the chatbot gives an incorrect answer, fails to reply, or cannot handle a case properly, please record the question and the chatbot response for review by the project team.

This pilot is intended to improve answer quality before wider rollout.
