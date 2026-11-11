import { t, useLocale } from '../../i18n'
import React, { useState } from 'react'
import styled from '@emotion/styled'
import type { OfficeHumanInputChange } from '../../services/office/officeEvents'

const Card = styled.fieldset`
  border: 1px solid #dce5f0; border-radius: 12px; padding: 15px 17px;
  margin: 0; background: #fafbfd; min-width: 0; color: #334155;
  legend { padding: 0 5px; font-size: 13px; font-weight: 600; line-height: 1.7; }
  label { display: flex; align-items: flex-start; gap: 10px; padding: 10px 3px;
    border-bottom: 1px solid #e8edf4; font-size: 13px; line-height: 1.7; cursor: pointer; }
  input[type=radio] { flex: 0 0 auto; margin: 5px 0 0; accent-color: #2563eb; }
  input[type=text] { box-sizing: border-box; width: 100%; min-width: 0; border: 0;
    border-bottom: 1px solid #c6d3e4; background: transparent; padding: 5px 0;
    color: inherit; font: inherit; }
  input:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
  small { display: block; margin-top: 10px; color: #718096; }
`

export default function HumanInputQuestion({ change, senderName, answer, disabled, onAnswer, onSubmit }: {
  change: OfficeHumanInputChange; senderName: string; answer: string; disabled: boolean
  onAnswer: (text: string) => void; onSubmit: (text: string) => void
}) {
  useLocale()
  const [custom, setCustom] = useState(false)
  const [customText, setCustomText] = useState('')
  const name = `choice:${change.runId}:${change.requestId}`
  return <Card data-office-human-input={change.requestId} disabled={disabled}>
    <legend>{senderName} · {change.question}</legend>
    {change.options.map(option => <label key={option.id}>
      <input type="radio" name={name} data-office-human-option={option.id}
        checked={!custom && answer === option.value}
        onChange={() => { setCustom(false); onAnswer(option.value) }} />
      <span>{option.label}</span>
    </label>)}
    <label>
      <input type="radio" name={name} aria-label={t('ui.custom_answer')} checked={custom}
        onChange={() => { setCustom(true); onAnswer(customText) }} />
      <span style={{ flex: 1, minWidth: 0 }}>{t('ui.custom_input')}<input type="text" aria-label={t('ui.your_custom_answer')} placeholder={t('ui.enter_your_feedback_or_use_the_chat_input_below')}
          value={custom ? answer : customText}
          onFocus={() => { if (!custom) { setCustom(true); onAnswer(customText) } }}
          onChange={event => { setCustomText(event.target.value); onAnswer(event.target.value) }}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.keyCode !== 229 && answer.trim()) {
              event.preventDefault(); onSubmit(answer)
            }
          }} />
      </span>
    </label>
    <small>{t('ui.choose_an_option_and_send_it_below_or_type_an_answer_selecting')}</small>
  </Card>
}
