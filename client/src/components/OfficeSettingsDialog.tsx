import { t, useLocale } from '../i18n'
// 本文件属于 Client 设置模块：只负责办公室设置的加载、编辑和保存。
// 不负责 Agent Loop、Run 调度、员工配置、Thread 或独立记忆存储。
// 关联：officeApi、DesktopBridge 与 DeerFlow Bootstrap/Memory API 共同完成设置读写。
import { useEffect, useState } from 'react'
import { Dialog, DialogTitle, DialogContent, DialogActions } from '@mui/material'
import OfficeModelSettings from './OfficeModelSettings'
import LanguageSelect from './LanguageSelect'
import styled from '@emotion/styled'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import SettingsRoundedIcon from '@mui/icons-material/SettingsRounded'

import {
  officeApi,
  type OfficeBootstrapStatus,
} from '../services/office/officeApi'

const Launcher = styled.button`
  position: fixed;
  top: 18px;
  right: 18px;
  z-index: 12000;
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.34);
  border-radius: 13px;
  color: #dbeafe;
  background: linear-gradient(145deg, #19304b, #10243a);
  box-shadow: 0 12px 26px rgba(15, 31, 49, 0.28);
  cursor: pointer;

  &:hover {
    color: #f7c873;
    transform: translateY(-1px);
  }

  &:focus-visible {
    outline: 3px solid rgba(247, 200, 115, 0.48);
    outline-offset: 3px;
  }
`

const Overlay = styled.div`
  position: fixed;
  inset: 0;
  z-index: 23000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  // Keep the office scene fully visible behind the settings panel. The overlay
  // still captures outside clicks, but must not paint a second full-screen layer.
  background: transparent;
  backdrop-filter: none;
`

const Panel = styled.section`
  position: relative;
  z-index: 1;
  width: min(820px, calc(100vw - 48px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.96);
  border-radius: 24px;
  color: #172b42;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbfb 100%);
  box-shadow: 0 30px 90px rgba(23, 43, 66, 0.28);
`

const Header = styled.header`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 26px 30px 21px;
  border-bottom: 1px solid #e2ecef;
  background: radial-gradient(circle at 92% 0%, rgba(199, 228, 223, 0.48), transparent 35%);

  h2,
  p {
    margin: 0;
  }

  h2 {
    font-size: 24px;
    letter-spacing: -0.02em;
  }

  p {
    margin-top: 7px;
    color: #7a8b9b;
    font-size: 13px;
  }
`

const Eyebrow = styled.div`
  margin-bottom: 5px;
  color: #2d8b85;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.16em;
`

const IconButton = styled.button`
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  color: #64748b;
  background: transparent;

  &:hover {
    color: #2d8b85;
    background: #edf5f4;
  }
`

const Body = styled.div`
  display: grid;
  gap: 14px;
  padding: 20px 30px 16px;
`

const Section = styled.section`
  padding: 18px 20px 20px;
  border: 1px solid #e0ebee;
  border-radius: 17px;
  background: rgba(255, 255, 255, 0.84);
  box-shadow: 0 8px 22px rgba(45, 139, 133, 0.06);
`

const SectionTitle = styled.div`
  margin-bottom: 14px;
  color: #172b42;
  font-size: 15px;
  font-weight: 800;
`

const Field = styled.label`
  display: grid;
  gap: 8px;
  color: #526b82;
  font-size: 11px;
  font-weight: 700;
`

const Input = styled.input`
  width: 100%;
  min-height: 43px;
  padding: 0 12px;
  border: 1px solid #d5e3e8;
  border-radius: 11px;
  outline: none;
  color: #30465d;
  background: #fff;
  font: inherit;
  font-weight: 400;

  &:focus {
    border-color: #5eb0aa;
    box-shadow: 0 0 0 3px rgba(94, 176, 170, 0.14);
  }
`

const TextArea = styled.textarea`
  width: 100%;
  min-height: 122px;
  padding: 11px 12px;
  border: 1px solid #d5e3e8;
  border-radius: 11px;
  outline: none;
  resize: vertical;
  color: #30465d;
  background: #fff;
  font: inherit;
  line-height: 1.65;

  &:focus {
    border-color: #5eb0aa;
    box-shadow: 0 0 0 3px rgba(94, 176, 170, 0.14);
  }
`








const Footer = styled.footer`
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 17px 30px 23px;
  border-top: 1px solid #e2ecef;
  background: rgba(245, 249, 249, 0.9);
  border-radius: 0 0 24px 24px;
`

const Button = styled.button<{ $primary?: boolean }>`
  min-height: 40px;
  padding: 0 19px;
  border: 1px solid ${(props) => (props.$primary ? '#2563eb' : '#cedae3')};
  border-radius: 11px;
  color: ${(props) => (props.$primary ? '#fff' : '#53687d')};
  background: ${(props) => (props.$primary ? '#2563eb' : '#fff')};
  box-shadow: ${(props) => (props.$primary ? '0 7px 14px rgba(37, 99, 235, 0.18)' : 'none')};
  font-size: 12px;
  font-weight: 750;

  &:disabled {
    cursor: wait;
    opacity: 0.62;
  }
`

type Props = {
  open: boolean
  onClose: () => void
}

function findPreferenceFact(memory: { facts: Array<{ id: string; content: string; category: string }> }) {
  return memory.facts.find((fact) => fact.category === 'user_preference') ?? null
}

export function OfficeSettingsLauncher({ onClick }: { onClick: () => void }) {
  useLocale()
  return (
    <Launcher type="button" aria-label={t('ui.open_office_settings')} onClick={onClick}>
      <SettingsRoundedIcon fontSize="small" />
    </Launcher>
  )
}

export default function OfficeSettingsDialog({ open, onClose }: Props) {
  useLocale()
  const [settings, setSettings] = useState<OfficeBootstrapStatus | null>(null)
  const [companyName, setCompanyName] = useState('')
  const [personalization, setPersonalization] = useState('')
  const [preferenceFactId, setPreferenceFactId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [terminalMode, setTerminalMode] = useState<'approval' | 'full_access'>('approval')
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false)
  const [showWarning, setShowWarning] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError('')
    setFullAccessConfirmed(false)
    setShowWarning(false)
    void Promise.all([officeApi.getSettings(), officeApi.getUserMemory()])
      .then(([nextSettings, memory]) => {
        setSettings(nextSettings)
        setCompanyName(nextSettings.companyName ?? '')
        setTerminalMode(nextSettings.terminalAccessMode ?? 'approval')
        const preference = findPreferenceFact(memory)
        setPreferenceFactId(preference?.id ?? null)
        setPersonalization(preference?.content ?? '')
      })
      .catch((nextError: unknown) => {
        setError(nextError instanceof Error ? nextError.message : t('ui.unable_to_load_office_settings'))
      })
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  const save = async () => {
    if (!settings?.initialized) {
      setError(t('ui.office_setup_is_incomplete'))
      return
    }
    if (!companyName.trim()) {
      setError(t('ui.office_name_is_required'))
      return
    }

    setSaving(true)
    setError('')
    try {
      await officeApi.saveSettings({
        companyName: companyName.trim(),
        terminalAccessMode: terminalMode,
        confirmFullAccess: fullAccessConfirmed,
      })

      const content = personalization.trim()
      if (content && preferenceFactId) {
        await officeApi.updateMemoryFact(preferenceFactId, {
          content,
          category: 'user_preference',
          confidence: 1,
        })
      } else if (content) {
        const memory = await officeApi.createMemoryFact({
          content,
          category: 'user_preference',
          confidence: 1,
        })
        const preference = findPreferenceFact(memory)
        setPreferenceFactId(preference?.id ?? null)
      } else if (preferenceFactId) {
        await officeApi.deleteMemoryFact(preferenceFactId)
        setPreferenceFactId(null)
      }
      onClose()
    } catch (nextError: unknown) {
      setError(nextError instanceof Error ? nextError.message : t('ui.unable_to_save_office_settings'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Overlay onMouseDown={event => { if (event.target === event.currentTarget && !showWarning) onClose() }}>
      <Panel data-office-settings-dialog role="dialog" aria-modal="true" aria-labelledby="office-settings-title" onMouseDown={(event) => event.stopPropagation()}>
        <Header>
          <div>
            <Eyebrow>OFFICE SETTINGS</Eyebrow>
            <h2 id="office-settings-title">{t('ui.office_settings')}</h2>
            <p>{t('ui.configure_your_office_name_default_model_and_personal_preferences')}</p>
          </div>
          <IconButton type="button" aria-label={t('ui.close_office_settings')} onClick={onClose}>
            <CloseRoundedIcon fontSize="small" />
          </IconButton>
        </Header>
        <Body>
          <LanguageSelect />
          <Section>
            <SectionTitle>{t('ui.office_information')}</SectionTitle>
            <Field>
              {t('ui.office_name')}<Input value={companyName} onChange={(event) => setCompanyName(event.target.value)} disabled={loading || saving} />
            </Field>
          </Section>

          <Section>
            <SectionTitle>{t('ui.model_settings')}</SectionTitle>
            <OfficeModelSettings />
          </Section>

          <Section>
            <SectionTitle>{t('ui.personalization')}</SectionTitle>
            <Field>
              {t('ui.personal_preferences')}<TextArea value={personalization} onChange={(event) => setPersonalization(event.target.value)} disabled={loading || saving} placeholder={t('ui.for_example_keep_answers_concise_and_lead_with_the_conclusion_do_not')} />
            </Field>
          </Section>
          <Section>
            <SectionTitle>{t('ui.powershell_permissions')}</SectionTitle>
            <p style={{ color: '#6a7e88', fontSize: 12, lineHeight: 1.7 }}>{t('ui.applies_to_employees_with_terminal_access_pending_requests_still_need_individual_approval')}</p>
            <div role="radiogroup" aria-label={t('ui.terminal_permissions')} style={{ display: 'flex', gap: 12 }}>
              {([['approval', t('ui.request_approval')], ['full_access', t('ui.full_access')]] as const).map(([value, label]) => (
                <label key={value} style={{ flex: 1, padding: 14, borderRadius: 12, border: `1px solid ${terminalMode === value ? '#5d9994' : '#dce6e8'}`, background: terminalMode === value ? '#edf6f3' : '#fff', fontSize: 14 }}>
                  <input type="radio" name="terminal-mode" value={value} checked={terminalMode === value} disabled={loading || saving}
                    onChange={() => value === 'full_access' ? setShowWarning(true) : setTerminalMode('approval')} /> {label}
                </label>
              ))}
            </div>
          </Section>
        </Body>
        {error && <p role="alert" style={{ color: "#b42318", padding: "0 30px" }}>{error}</p>}
        <Footer>
          <Button type="button" onClick={onClose} disabled={saving}>{t('ui.cancel')}</Button>
          <Button type="button" $primary onClick={() => void save()} disabled={loading || saving}>{saving ? t('ui.saving_settings') : t('ui.save_settings')}</Button>
        </Footer>
      </Panel>
      <Dialog open={showWarning} onClose={() => setShowWarning(false)} onMouseDown={event => event.stopPropagation()} sx={{ zIndex: 25000 }} maxWidth="xs" fullWidth
        aria-labelledby="full-access-title" PaperProps={{ sx: { borderRadius: 4, background: '#fffdf8', color: '#493b30' } }}>
        <DialogTitle id="full-access-title">{t('ui.enable_full_access')}</DialogTitle>
        <DialogContent><p style={{ lineHeight: 1.8 }}>{t('ui.employees_can_run_local_commands_install_dependencies_and_delete_or_modify_files')}</p><p>{t('ui.enable_only_for_trusted_projects_and_instructions_save_settings_to_apply')}</p></DialogContent>
        <DialogActions sx={{ p: 2.5 }}><Button onClick={() => setShowWarning(false)}>{t('ui.cancel')}</Button><Button $primary onClick={() => { setTerminalMode('full_access'); setFullAccessConfirmed(true); setShowWarning(false) }}>{t('ui.understand_and_continue')}</Button></DialogActions>
      </Dialog>
    </Overlay>
  )
}
