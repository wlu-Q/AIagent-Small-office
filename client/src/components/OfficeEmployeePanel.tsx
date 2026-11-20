import { t, useLocale, localizedTemplate } from '../i18n'
import React, { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import AddRoundedIcon from '@mui/icons-material/AddRounded'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import HomeWorkOutlinedIcon from '@mui/icons-material/HomeWorkOutlined'
import PersonAddAlt1Icon from '@mui/icons-material/PersonAddAlt1'
import CheckRoundedIcon from '@mui/icons-material/CheckRounded'
import AutoStoriesOutlinedIcon from '@mui/icons-material/AutoStoriesOutlined'

import {
  officeApi,
  type OfficeEmployeeTemplateSummary,
  type OfficeSkillSummary,
} from '../services/office/officeApi'
import { randomEmployeeName } from '../utils/randomEmployeeName'
import { additionalCharacterTextures } from '../characters/additionalCharacterTextures'
import { DAILY_FRAME_HEIGHT, DAILY_FRAME_WIDTH, DAILY_FRONT_IDLE_FRAME, dailyCharacterImage, dailyFrameOffset } from '../characters/dailyCharacterAssets'
import OfficeEmployeeEditor from './OfficeEmployeeEditor'

const FloatingActions = styled.div`
  position: fixed;
  left: 16px;
  top: 148px;
  z-index: 12000;
  display: grid;
  gap: 10px;
`

const LaunchButton = styled.button`
  width: 54px;
  height: 54px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.68);
  border-radius: 17px;
  background: linear-gradient(145deg, #19304b, #10243a);
  color: #f7c873;
  box-shadow: 0 16px 28px rgba(15, 31, 49, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.12);
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;

  &:hover {
    background: linear-gradient(145deg, #244765, #142f49);
    transform: translateY(-2px);
    box-shadow: 0 19px 34px rgba(15, 31, 49, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.14);
  }

  &:focus-visible {
    outline: 3px solid rgba(247, 200, 115, 0.48);
    outline-offset: 3px;
  }
`

const Overlay = styled.div<{ $nested?: boolean }>`
  position: fixed;
  inset: 0;
  z-index: ${(props) => (props.$nested ? 22000 : 20500)};
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 28px 20px;
  background: ${(props) => (
    props.$nested
      ? 'rgba(13, 26, 41, 0.62)'
      : 'radial-gradient(circle at 14% 8%, rgba(247, 200, 115, 0.12), transparent 24%), rgba(10, 21, 34, 0.62)'
  )};
  backdrop-filter: blur(8px);
`

const Panel = styled.section`
  width: min(1180px, calc(100vw - 40px));
  height: min(780px, calc(100vh - 56px));
  min-height: 520px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 24px;
  background: #f6f8fb;
  color: #172b42;
  box-shadow: 0 36px 90px rgba(3, 14, 27, 0.34), 0 0 0 8px rgba(255, 255, 255, 0.08);

  @media (max-width: 760px) {
    width: calc(100vw - 24px);
    height: calc(100vh - 32px);
    min-height: 0;
  }
`

const Header = styled.header`
  min-height: 76px;
  flex: 0 0 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 24px;
  border-bottom: 1px solid #e3e9f0;
  background: linear-gradient(110deg, #ffffff 0%, #f9fbfd 64%, #f3f7f8 100%);
`

const HeaderIdentity = styled.div`
  display: flex;
  align-items: center;
  gap: 11px;
`

const HeaderIcon = styled.span`
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 1px solid #f2d49e;
  border-radius: 13px;
  background: #fff7e6;
  color: #a86c16;
  box-shadow: 0 6px 14px rgba(168, 108, 22, 0.1);
`

const HeaderCopy = styled.div`
  h2,
  p {
    margin: 0;
  }

  h2 {
    color: #172b42;
    font-size: 19px;
    font-weight: 850;
    letter-spacing: -0.02em;
  }

  p {
    margin-top: 5px;
    color: #7a8a9d;
    font-size: 11px;
  }
`

const HeaderActions = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
`

const PrimaryButton = styled.button`
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 13px;
  border: 0;
  border-radius: 10px;
  background: #2563eb;
  color: #ffffff;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 750;

  &:hover {
    background: #1d4ed8;
  }

  &:disabled {
    background: #b9c5d4;
    cursor: not-allowed;
  }
`

const IconButton = styled.button`
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #64748b;
  cursor: pointer;

  &:hover {
    background: #e2e8f0;
    color: #0f172a;
  }
`

const Workspace = styled.div`
  min-height: 0;
  flex: 1 1 0;
  display: flex;
`

const EmployeeListPane = styled.aside`
  width: 304px;
  flex: 0 0 304px;
  min-height: 0;
  overflow: auto;
  padding: 22px 14px;
  border-right: 1px solid #e3e9f0;
  background: #edf2f6;

  @media (max-width: 760px) {
    width: 220px;
    flex-basis: 220px;
  }
`

const SectionLabel = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px 12px;
  color: #74869b;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.1em;
  text-transform: uppercase;
`

const EmployeeList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 7px;
`

const EmployeeListItem = styled.button<{ $active: boolean }>`
  width: 100%;
  min-height: 66px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 11px;
  border: 1px solid ${(props) => (props.$active ? '#d8e4ed' : 'transparent')};
  border-radius: 15px;
  background: ${(props) => (props.$active ? '#ffffff' : 'transparent')};
  color: #172b42;
  text-align: left;
  cursor: pointer;
  box-shadow: ${(props) => (props.$active ? '0 8px 20px rgba(35, 58, 80, 0.08)' : 'none')};
  transition: background 150ms ease, transform 150ms ease, box-shadow 150ms ease;

  &:hover {
    background: #ffffff;
    transform: translateX(2px);
  }

  &:focus-visible {
    outline: 3px solid rgba(65, 148, 143, 0.28);
    outline-offset: 2px;
  }
`

const EmployeeAvatar = styled.div`
  width: 43px;
  height: 43px;
  flex: 0 0 43px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 2px solid rgba(255, 255, 255, 0.86);
  border-radius: 14px;
  background: linear-gradient(145deg, #d9eceb, #b9d9d7);
  color: #246a69;
  box-shadow: 0 5px 12px rgba(36, 106, 105, 0.12);
  font-size: 16px;
  font-weight: 850;
`

const EmployeeAvatarImage = styled.img`
  width: 100%;
  height: 100%;
  object-fit: cover;
  image-rendering: pixelated;
`

const EmployeeListCopy = styled.span`
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;

  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    font-size: 13px;
    font-weight: 750;
  }

  small {
    color: #718096;
    font-size: 11px;
  }
`

const EmptyList = styled.div`
  padding: 20px 10px;
  border: 1px dashed rgba(148, 163, 184, 0.34);
  border-radius: 12px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
`

const DetailPane = styled.main`
  min-width: 0;
  min-height: 0;
  flex: 1 1 0;
  overflow: auto;
  padding: 30px 38px 34px;
  background: #fbfcfd;

  @media (max-width: 760px) {
    padding: 24px 20px 28px;
  }
`

const DetailHeading = styled.div`
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 4px 0 25px;
  border-bottom: 1px solid #e6ebf0;
`

const LargeAvatar = styled(EmployeeAvatar)`
  width: 60px;
  height: 60px;
  flex-basis: 60px;
  border-radius: 18px;
  font-size: 22px;
`

const DetailName = styled.div`
  min-width: 0;

  h3,
  p {
    margin: 0;
  }

  h3 {
    overflow: hidden;
    color: #172b42;
    font-size: 25px;
    font-weight: 850;
    letter-spacing: -0.03em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  p {
    margin-top: 6px;
    color: #7a8a9d;
    font-size: 12px;
  }
`

const DetailStatus = styled.span<{ $disabled?: boolean }>`
  margin-left: auto;
  flex: 0 0 auto;
  padding: 6px 10px;
  border-radius: 999px;
  background: ${(props) => (props.$disabled ? '#fee2e2' : '#dcfce7')};
  color: ${(props) => (props.$disabled ? '#b91c1c' : '#15803d')};
  font-size: 11px;
  font-weight: 750;
`

const FieldSection = styled.section`
  padding: 22px 0;
  border-bottom: 1px solid #e9eef2;
`

const FieldTitle = styled.h4`
  margin: 0 0 10px;
  color: #61748a;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.12em;
  text-transform: uppercase;
`

const FieldTitleRow = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
`

const FieldTitleText = styled.h4`
  margin: 0;
  color: #61748a;
  font-size: 11px;
  font-weight: 850;
  letter-spacing: 0.12em;
  text-transform: uppercase;
`

const SkillActions = styled.div`
  position: relative;
  flex: 0 0 auto;
`

const SkillMenu = styled.div`
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 3;
  display: grid;
  gap: 4px;
  width: 260px;
  max-height: 280px;
  overflow: auto;
  padding: 9px;
  border: 1px solid #d7e3e8;
  border-radius: 13px;
  background: #ffffff;
  box-shadow: 0 18px 35px rgba(24, 45, 64, 0.16);
`

const SkillMenuItem = styled.button`
  padding: 8px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #4a6076;
  text-align: left;
  cursor: pointer;
  font: inherit;
  font-size: 12px;

  &:hover:not(:disabled) {
    background: #f1f7f8;
  }

  &:disabled {
    color: #b1bcc7;
    cursor: not-allowed;
  }
`

const ReadonlyBox = styled.div`
  min-height: 60px;
  padding: 14px 15px;
  border: 1px solid #e1e8ed;
  border-radius: 13px;
  background: #ffffff;
  color: #52657f;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  box-shadow: 0 5px 14px rgba(29, 55, 75, 0.035);
`

const TextArea = styled.textarea`
  width: 100%;
  min-height: 150px;
  box-sizing: border-box;
  resize: vertical;
  padding: 14px 15px;
  border: 1px solid #cfdae3;
  border-radius: 13px;
  outline: none;
  background: #ffffff;
  color: #243b53;
  font: inherit;
  font-size: 13px;
  line-height: 1.7;
  transition: border-color 150ms ease, box-shadow 150ms ease;

  &:focus {
    border-color: #4c9b98;
    box-shadow: 0 0 0 4px rgba(76, 155, 152, 0.14);
  }
`

const TagList = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
`

const Tag = styled.span`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 7px 10px;
  border: 1px solid #c9e3df;
  border-radius: 999px;
  background: #eff9f7;
  color: #287d79;
  font-size: 11px;
  font-weight: 700;

  svg {
    font-size: 14px;
  }
`

const FooterNote = styled.p`
  margin: 18px 0 0;
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.6;
`

const CreateOverlay = styled(Overlay)`
  align-items: center;
  padding: 18px;
`

const CreateDialog = styled.form`
  width: min(640px, calc(100vw - 32px));
  max-height: min(720px, calc(100vh - 36px));
  overflow: auto;
  padding: 26px;
  border: 1px solid rgba(255, 255, 255, 0.74);
  border-radius: 20px;
  background: #fbfcfd;
  box-shadow: 0 30px 80px rgba(5, 18, 31, 0.34);
`

const DialogHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;

  h3 {
    margin: 0;
    color: #0f172a;
    font-size: 18px;
  }
`

const FormGrid = styled.div`
  display: grid;
  gap: 13px;
`

const FormField = styled.label`
  display: grid;
  gap: 7px;
  color: #475569;
  font-size: 12px;
  font-weight: 700;

  input,
  select {
    width: 100%;
    min-height: 42px;
    box-sizing: border-box;
    padding: 0 11px;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    outline: none;
    background: #ffffff;
    color: #1e293b;
    font: inherit;
    font-weight: 400;

    &:focus {
      border-color: #60a5fa;
      box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.14);
    }
  }
`

const frontOffset = dailyFrameOffset(DAILY_FRONT_IDLE_FRAME)
const AppearancePreview = styled.div`
  display: grid;
  place-items: center;
  min-height: ${DAILY_FRAME_HEIGHT + 24}px;
  border: 1px solid #dbe4ee;
  border-radius: 10px;
  background: #f1f5f9;

  > span {
    width: ${DAILY_FRAME_WIDTH}px;
    height: ${DAILY_FRAME_HEIGHT}px;
    background-position: ${-frontOffset.x}px ${-frontOffset.y}px;
    background-repeat: no-repeat;
    image-rendering: pixelated;
  }
`

const DialogFooter = styled.div`
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 21px;
  padding-top: 15px;
  border-top: 1px solid #e2e8f0;
`

const EditHint = styled.p`
  margin: -2px 0 0;
  color: #8191a3;
  font-size: 11px;
  line-height: 1.6;
`

const QuietButton = styled.button`
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 0 13px;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #ffffff;
  color: #475569;
  cursor: pointer;
  font: inherit;
  font-size: 12px;

  &:hover {
    background: #f8fafc;
  }
`

const FormMessage = styled.p`
  margin: 13px 0 0;
  color: #b42318;
  font-size: 12px;
`

function createEmployeeId(templateId: string) {
  return templateId + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7)
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

export default function OfficeEmployeePanel({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  useLocale()
  const [open, setOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [templates, setTemplates] = useState<OfficeEmployeeTemplateSummary[]>([])
  const [skills, setSkills] = useState<OfficeSkillSummary[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>()
  const [promptByTemplate, setPromptByTemplate] = useState<Record<string, string>>({})
  const [skillsByTemplate, setSkillsByTemplate] = useState<Record<string, string[]>>({})
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [templateId, setTemplateId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [avatar, setAvatar] = useState<string>(additionalCharacterTextures[0])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!window.officeDesktopBridge) return
    void Promise.all([
      officeApi.listEmployeeTemplates(),
      officeApi.listSkills(),
    ]).then(([availableTemplates, availableSkills]) => {
      setTemplates(availableTemplates)
      setSkills(availableSkills)
      setSelectedTemplateId(availableTemplates[0]?.templateId)
      setTemplateId(availableTemplates[0]?.templateId || '')
    }).catch((loadError) => setError(errorText(loadError)))
  }, [])

  useEffect(() => {
    if (!open || !window.officeDesktopBridge) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = async () => {
      try {
        const available = await officeApi.listSkills()
        if (active) setSkills(available)
      } catch (loadError) {
        if (active) setError(errorText(loadError))
      } finally {
        if (active) timer = setTimeout(refresh, 1500)
      }
    }
    void refresh()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [open])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (editOpen) return
      if (createOpen) setCreateOpen(false)
      else if (open) setOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [createOpen, editOpen, open])

  const localizedTemplates = templates.map(localizedTemplate)
  const selectedTemplate = localizedTemplates.find((template) => template.templateId === selectedTemplateId)
  const selectedPrompt = selectedTemplate
    ? promptByTemplate[selectedTemplate.templateId] ?? selectedTemplate.prompt ?? ''
    : ''
  const selectedSkills = selectedTemplate
    ? skillsByTemplate[selectedTemplate.templateId] ?? selectedTemplate.skills ?? []
    : []
  const defaultSkills = selectedTemplate?.skills ?? []
  const availableSkills = skills.map((skill) => skill.name)

  const selectTemplate = (nextTemplate: OfficeEmployeeTemplateSummary) => {
    setSelectedTemplateId(nextTemplate.templateId)
    setTemplateId(nextTemplate.templateId)
    setSkillsOpen(false)
    setError('')
  }

  const openCreateDialog = () => {
    if (!selectedTemplate) return
    setError('')
    setDisplayName(randomEmployeeName())
    setAvatar(additionalCharacterTextures[Math.floor(Math.random() * additionalCharacterTextures.length)])
    setTemplateId(selectedTemplate.templateId)
    setCreateOpen(true)
  }

  const createEmployee = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      await officeApi.createEmployee({
        id: createEmployeeId(templateId),
        templateId,
        displayName: displayName.trim(),
        avatar,
        prompt: selectedPrompt,
        skills: selectedSkills.filter((name) => skills.some((skill) => skill.name === name && skill.enabled)),
      })
      await officeApi.listEmployees({ fresh: true })
      setCreateOpen(false)
    } catch (createError) {
      setError(errorText(createError))
    }
  }

  const updatePrompt = (value: string) => {
    if (!selectedTemplate) return
    setPromptByTemplate((current) => ({ ...current, [selectedTemplate.templateId]: value }))
  }

  const addSkill = (skill: string) => {
    if (!selectedTemplate || selectedSkills.includes(skill)) return
    setSkillsByTemplate((current) => ({
      ...current,
      [selectedTemplate.templateId]: [...selectedSkills, skill],
    }))
    setSkillsOpen(false)
  }

  const removeSkill = (skill: string) => {
    if (!selectedTemplate || defaultSkills.includes(skill)) return
    setSkillsByTemplate((current) => ({
      ...current,
      [selectedTemplate.templateId]: selectedSkills.filter((item) => item !== skill),
    }))
  }

  return (
    <>
      <FloatingActions>
        <LaunchButton
          type="button"
          aria-label={t('ui.open_recruitment')}
          data-office-employee-manager-launcher
          onClick={() => setOpen(true)}
        >
          <PersonAddAlt1Icon />
        </LaunchButton>
        <LaunchButton
          type="button"
          aria-label={t('ui.open_employees')}
          data-office-employee-editor-launcher
          onClick={() => setEditOpen(true)}
        >
          <HomeWorkOutlinedIcon fontSize="small" />
        </LaunchButton>
        <LaunchButton
          type="button"
          aria-label={t('ui.open_office_library')}
          title={t('ui.office_library')}
          data-office-library-launcher
          onClick={onOpenLibrary}
        >
          <AutoStoriesOutlinedIcon fontSize="small" />
        </LaunchButton>
      </FloatingActions>

      <Overlay style={{ pointerEvents: open ? 'auto' : 'none', opacity: open ? 1 : 0, visibility: open ? 'visible' : 'hidden' }}>
        <Panel role="dialog" aria-modal="true" aria-labelledby="office-hire-center-title" data-office-employee-manager onMouseDown={(event) => event.stopPropagation()}>
          <Header>
            <HeaderIdentity>
              <HeaderIcon><HomeWorkOutlinedIcon fontSize="small" /></HeaderIcon>
              <HeaderCopy>
                <h2 id="office-hire-center-title">{t('ui.recruitment')}</h2>
                <p>{t('ui.choose_a_role_review_its_capabilities_and_create_an_employee')}</p>
              </HeaderCopy>
            </HeaderIdentity>
            <HeaderActions>
            <IconButton type="button" aria-label={t('ui.close_recruitment')} onClick={() => setOpen(false)}>
              <CloseRoundedIcon fontSize="small" />
            </IconButton>
            </HeaderActions>
          </Header>

          <Workspace>
            <EmployeeListPane>
              <SectionLabel><span>{t('ui.available_roles')}</span><span>{t('templates.count', { count: templates.length })}</span></SectionLabel>
              <EmployeeList>
                {localizedTemplates.map((template) => (
                  <EmployeeListItem
                    type="button"
                    key={template.templateId}
                    $active={template.templateId === selectedTemplateId}
                    aria-pressed={template.templateId === selectedTemplateId}
                    data-employee-template-item={template.templateId}
                    onClick={() => selectTemplate(template)}
                  >
                    <EmployeeAvatar>{template.role.slice(0, 1)}</EmployeeAvatar>
                    <EmployeeListCopy>
                      <strong>{template.role}</strong>
                      <small>{template.category} · {template.tags[0] || t('ui.built_in_role')}</small>
                    </EmployeeListCopy>
                  </EmployeeListItem>
                ))}
              </EmployeeList>
              {templates.length === 0 && <EmptyList>{t('ui.no_roles_available')}</EmptyList>}
            </EmployeeListPane>

            <DetailPane>
              {selectedTemplate ? (
                <>
                  <DetailHeading>
                    <LargeAvatar>{selectedTemplate.role.slice(0, 1)}</LargeAvatar>
                    <DetailName>
                      <h3>{selectedTemplate.role}</h3>
                      <p>{selectedTemplate.category} {t('ui.built_in_role_template')}</p>
                    </DetailName>
                    <HeaderActions>
                      <PrimaryButton type="button" data-create-employee-open onClick={openCreateDialog}>
                        <AddRoundedIcon fontSize="small" />
                        {t('ui.hire_for_this_role')}</PrimaryButton>
                    </HeaderActions>
                  </DetailHeading>

                  <FieldSection>
                    <FieldTitle>{t('ui.role_tags')}</FieldTitle>
                    <TagList>
                      {selectedTemplate.tags.map((tag) => <Tag key={tag}><CheckRoundedIcon fontSize="small" />{tag}</Tag>)}
                    </TagList>
                  </FieldSection>

                  <FieldSection>
                    <FieldTitle>{t('ui.specialties')}</FieldTitle>
                    {selectedTemplate.specialties.length > 0 ? (
                      <TagList>{selectedTemplate.specialties.map((specialty) => <Tag key={specialty}>{specialty}</Tag>)}</TagList>
                    ) : (
                      <ReadonlyBox>{t('ui.no_specialties_provided_by_this_template')}</ReadonlyBox>
                    )}
                  </FieldSection>

                  <FieldSection>
                    <FieldTitle>Prompt <span style={{ color: '#2a8582', letterSpacing: 0 }}>{t('ui.editable')}</span></FieldTitle>
                    <TextArea
                      data-template-prompt
                      value={selectedPrompt}
                      placeholder={t('ui.no_prompt_is_available_for_this_template_yet')}
                      onChange={(event) => updatePrompt(event.target.value)}
                    />
                    <FooterNote>{t('ui.edits_apply_to_this_hire_s_agent_package_and_do_not_modify')}</FooterNote>
                  </FieldSection>

                   <FieldSection>
                     <FieldTitleRow>
                       <FieldTitleText>Skills <span style={{ color: '#2a8582', letterSpacing: 0 }}>{selectedSkills.length}</span></FieldTitleText>
                       <SkillActions>
                         <QuietButton type="button" data-add-skill onClick={() => setSkillsOpen((current) => !current)}>
                           <AddRoundedIcon fontSize="small" />{t('ui.add')}</QuietButton>
                         {skillsOpen && (
                           <SkillMenu>
                             {availableSkills.length > 0 ? availableSkills.map((skill) => (
                               <SkillMenuItem key={skill} type="button" disabled={selectedSkills.includes(skill)} onClick={() => addSkill(skill)}>
                                 {skill}{selectedSkills.includes(skill) ? t('ui.added') : ''}
                               </SkillMenuItem>
                             )) : <span style={{ padding: '8px 9px', color: '#96a3b0', fontSize: 12 }}>{t('ui.no_skills_available_to_add')}</span>}
                           </SkillMenu>
                         )}
                       </SkillActions>
                     </FieldTitleRow>
                    {selectedSkills.length > 0 ? (
                      <TagList>
                         {selectedSkills.map((skill) => <Tag key={skill}><CheckRoundedIcon fontSize="small" />{skill}{!defaultSkills.includes(skill) && <IconButton type="button" aria-label={t('ui.remove') + skill} onClick={() => removeSkill(skill)}><CloseRoundedIcon fontSize="small" /></IconButton>}</Tag>)}
                      </TagList>
                    ) : (
                      <ReadonlyBox>{t('ui.no_initial_skills_are_provided_by_this_template_yet')}</ReadonlyBox>
                    )}
                    <FooterNote>{t('ui.hiring_keeps_the_template_s_default_skills_add_personal_skills_without_changing')}</FooterNote>
                  </FieldSection>
                </>
              ) : (
                <EmptyList>{t('ui.select_a_role_on_the_left_to_view_details')}</EmptyList>
              )}
            </DetailPane>
          </Workspace>
          {error && <FormMessage role="alert" data-office-employee-error>{error}</FormMessage>}
        </Panel>
      </Overlay>

      <OfficeEmployeeEditor open={editOpen} onClose={() => setEditOpen(false)} />
      {createOpen && selectedTemplate && (
        <CreateOverlay $nested onMouseDown={() => setCreateOpen(false)}>
          <CreateDialog onSubmit={createEmployee} onMouseDown={(event) => event.stopPropagation()}>
            <DialogHeader>
              <h3>{t('ui.hire')}{selectedTemplate.role}</h3>
              <IconButton type="button" aria-label={t('ui.close_hiring_dialog')} onClick={() => setCreateOpen(false)}><CloseRoundedIcon fontSize="small" /></IconButton>
            </DialogHeader>
            <FormGrid>
              <FormField>{t('ui.role')}<ReadonlyBox data-employee-template>{selectedTemplate.role}</ReadonlyBox></FormField>
              <FormField>{t('ui.name')}<input data-create-employee-name placeholder={t('ui.name')} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></FormField>
              <FormField>
                {t('ui.employee_appearance')}
                <select value={avatar} onChange={(event) => setAvatar(event.currentTarget.value)}>
                  {additionalCharacterTextures.map((texture) => (
                    <option key={texture} value={texture}>{texture[0].toUpperCase() + texture.slice(1)}</option>
                  ))}
                </select>
              </FormField>
              <AppearancePreview>
                <span role="img" aria-label={avatar} style={{ backgroundImage: `url("${dailyCharacterImage(avatar)}")` }} />
              </AppearancePreview>
            </FormGrid>
            <DialogFooter>
              <QuietButton type="button" onClick={() => setCreateOpen(false)}>{t('ui.cancel')}</QuietButton>
              <PrimaryButton type="submit" data-create-employee disabled={!templateId || !displayName.trim()}>{t('ui.confirm_hire')}</PrimaryButton>
            </DialogFooter>
          </CreateDialog>
        </CreateOverlay>
      )}
    </>
  )
}
