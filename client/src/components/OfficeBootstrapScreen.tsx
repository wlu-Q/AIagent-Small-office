import { t, useLocale } from '../i18n'
import LanguageSelect from './LanguageSelect'
import React, { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import ApartmentRoundedIcon from '@mui/icons-material/ApartmentRounded'
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded'
import VisibilityOffRoundedIcon from '@mui/icons-material/VisibilityOffRounded'
import VisibilityRoundedIcon from '@mui/icons-material/VisibilityRounded'
import FolderOpenRoundedIcon from '@mui/icons-material/FolderOpenRounded'

import { officeApi, type OfficeBootstrapInput } from '../services/office/officeApi'
import { refreshWorkspaceSummary, selectAndBindWorkspace, subscribeWorkspaceSummary } from '../services/desktop/workspaceBridge'
import type { WorkspaceSummary } from '../services/desktop/types'

const Overlay = styled.main`
  position: fixed;
  inset: 0;
  z-index: 24000;
  display: grid;
  place-items: center;
  padding: 28px;
  overflow: hidden;
  background:
    radial-gradient(circle at 16% 18%, rgba(255, 255, 255, 0.9), transparent 27%),
    linear-gradient(150deg, #bfe8f7 0%, #dff4f8 52%, #f4f7f2 100%);
  color: #172b42;

  &::before {
    content: '';
    position: absolute;
    inset: 0;
    opacity: 0.22;
    background-image: radial-gradient(rgba(38, 79, 106, 0.22) 1px, transparent 1px);
    background-size: 18px 18px;
    mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.65), transparent 72%);
    pointer-events: none;
  }
`

const Cloud = styled.div<{ $top: string; $duration: string; $delay: string; $scale: number }>`
  position: absolute;
  top: ${(props) => props.$top};
  left: -150px;
  width: 112px;
  height: 22px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.56);
  box-shadow: -24px 7px 0 rgba(255, 255, 255, 0.5), 28px 5px 0 rgba(255, 255, 255, 0.52);
  opacity: 0.72;
  transform: scale(${(props) => props.$scale});
  animation: bootstrap-cloud-drift ${(props) => props.$duration} linear ${(props) => props.$delay} infinite;
  pointer-events: none;

  @keyframes bootstrap-cloud-drift {
    from { margin-left: 0; }
    to { margin-left: calc(100vw + 320px); }
  }
`

const OfficeHorizon = styled.div`
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 22%;
  opacity: 0.18;
  background:
    linear-gradient(to top, rgba(38, 79, 106, 0.48), transparent),
    repeating-linear-gradient(to right, transparent 0 56px, rgba(38, 79, 106, 0.55) 56px 60px, transparent 60px 108px);
  clip-path: polygon(0 58%, 8% 58%, 8% 36%, 16% 36%, 16% 52%, 24% 52%, 24% 21%, 31% 21%, 31% 49%, 41% 49%, 41% 31%, 49% 31%, 49% 56%, 57% 56%, 57% 18%, 65% 18%, 65% 47%, 75% 47%, 75% 27%, 84% 27%, 84% 54%, 92% 54%, 92% 34%, 100% 34%, 100% 100%, 0 100%);
  pointer-events: none;
`

const Card = styled.section`
  position: relative;
  z-index: 1;
  width: min(580px, 100%);
  max-height: calc(100vh - 56px);
  border: 1px solid #b8cce0;
  border-radius: 18px;
  background: rgba(250, 253, 255, 0.96);
  box-shadow: 0 20px 52px rgba(30, 61, 92, 0.2);
  overflow: auto;
`

const Accent = styled.div`
  height: 6px;
  background: #2563eb;
`

const Content = styled.div`
  padding: 24px 32px;

  @media (max-width: 560px) {
    padding: 24px 20px;
  }
`

const Heading = styled.header`
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;

  h1 {
    margin: 0;
    font-size: 26px;
    line-height: 1.2;
  }

  p {
    margin: 6px 0 0;
    color: #718096;
    font-size: 14px;
    line-height: 1.55;
  }
`

const IconBox = styled.div`
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  display: grid;
  place-items: center;
  border: 1px solid #f2d49e;
  border-radius: 14px;
  background: #fff7e6;
  color: #c47b12;
`

const Form = styled.form`
  display: grid;
  gap: 14px;
`

const Field = styled.label`
  display: grid;
  gap: 8px;
  color: #475569;
  font-size: 13px;
  font-weight: 700;

  input {
    width: 100%;
    box-sizing: border-box;
    min-height: 44px;
    border: 1px solid #cbd8e6;
    border-radius: 9px;
    background: #fff;
    color: #243b53;
    padding: 10px 12px;
    font: inherit;
    font-weight: 400;
    outline: none;
  }

  input:focus {
    border-color: #4c9b98;
    box-shadow: 0 0 0 3px rgba(76, 155, 152, 0.13);
  }
`

const SectionTitle = styled.div`
  padding-top: 4px;
  color: #172b42;
  font-size: 16px;
  font-weight: 800;
`

const KeyRow = styled.div`
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  align-items: end;
`

const PasswordWrap = styled.div`
  position: relative;

  input {
    padding-right: 46px;
  }

  button {
    position: absolute;
    right: 4px;
    top: 4px;
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: #7890aa;
    cursor: pointer;
  }
`

const TestButton = styled.button`
  min-height: 44px;
  border: 1px solid #2563eb;
  border-radius: 9px;
  background: #fff;
  color: #2563eb;
  padding: 0 14px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;

  &:disabled {
    border-color: #b9c5d4;
    color: #8da0b5;
    cursor: not-allowed;
  }
`

const StatusLine = styled.div<{ $error?: boolean }>`
  min-height: 20px;
  display: flex;
  align-items: center;
  gap: 7px;
  color: ${(props) => (props.$error ? '#b42318' : '#287d79')};
  font-size: 13px;
  line-height: 1.5;
`

const WorkspaceRow = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;

  input { text-overflow: ellipsis; }
  button { display: flex; align-items: center; gap: 6px; }
`

const WorkspaceHint = styled.span`
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
  color: #718096;
`

const EnterButton = styled.button`
  min-height: 48px;
  border: 0;
  border-radius: 9px;
  background: #2563eb;
  color: #fff;
  font: inherit;
  font-size: 16px;
  font-weight: 800;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: #1d4ed8;
  }

  &:disabled {
    background: #b9c5d4;
    cursor: not-allowed;
  }
`

type Props = {
  onInitialized: React.Dispatch<React.SetStateAction<boolean>>
}

function initialInput(): OfficeBootstrapInput {
  return { companyName: '', modelName: '', baseUrl: '', apiKey: '' }
}

export default function OfficeBootstrapScreen({ onInitialized }: Props) {
  useLocale()
  const [input, setInput] = useState<OfficeBootstrapInput>(initialInput)
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState(false)
  const [initializing, setInitializing] = useState(false)
  const [tested, setTested] = useState(false)
  const [message, setMessage] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [workspace, setWorkspace] = useState<WorkspaceSummary>()
  const [binding, setBinding] = useState(false)
  const [workspaceError, setWorkspaceError] = useState('')

  useEffect(() => {
    let active = true
    const unsubscribe = subscribeWorkspaceSummary(setWorkspace)
    void officeApi.getBootstrap()
      .then(async (status) => {
        if (!active) return
        if (status.initialized) {
          onInitialized(true)
          return
        }
        await refreshWorkspaceSummary()
        if (active) setLoading(false)
      })
      .catch((error) => {
        if (!active) return
        setLoading(false)
        setMessage(error instanceof Error ? error.message : t('ui.unable_to_load_office_setup_status'))
      })
    return () => { active = false; unsubscribe() }
  }, [onInitialized])

  const update = (key: keyof OfficeBootstrapInput, value: string) => {
    setInput((current) => ({ ...current, [key]: value }))
    setTested(false)
    setMessage('')
  }
  const readyToTest = Object.values(input).every((value) => value.trim())

  const handleBindWorkspace = async () => {
    setBinding(true)
    setWorkspaceError('')
    try {
      await selectAndBindWorkspace()
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '') : t('ui.unable_to_bind_workspace_select_the_folder_again'))
    } finally {
      setBinding(false)
    }
  }

  const handleTest = async () => {
    if (!readyToTest) return
    setTesting(true)
    setTested(false)
    setMessage('')
    try {
      await officeApi.testBootstrapModel(input)
      setTested(true)
      setMessage(t('ui.connected_you_can_now_build_your_team'))
    } catch (error) {
      setMessage(error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '') : t('ui.model_connection_failed_check_the_settings'))
    } finally {
      setTesting(false)
    }
  }

  const handleInitialize = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!tested || !workspace || binding || initializing) return
    setInitializing(true)
    setMessage('')
    try {
      await officeApi.initializeBootstrap(input)
      await officeApi.listEmployees({ fresh: true })
      onInitialized(true)
    } catch (error) {
      setTested(false)
      setMessage(error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '') : t('ui.setup_failed_test_the_model_connection_again'))
    } finally {
      setInitializing(false)
    }
  }

  if (loading) {
    return <Overlay aria-busy="true">{t('ui.preparing_your_office')}</Overlay>
  }

  return (
    <Overlay>
      <Cloud $top="18%" $duration="36s" $delay="-12s" $scale={0.8} />
      <Cloud $top="31%" $duration="44s" $delay="-28s" $scale={1.15} />
      <Cloud $top="12%" $duration="52s" $delay="-39s" $scale={0.58} />
      <OfficeHorizon />
      <Card data-office-bootstrap>
        <Accent />
        <Content>
          <Heading>
            <IconBox><ApartmentRoundedIcon /></IconBox>
            <div>
              <LanguageSelect />
              <h1>{t('ui.create_your_office')}</h1>
              <p>{t('ui.build_your_own_office_of_ai_employees')}</p>
            </div>
          </Heading>
          <Form onSubmit={handleInitialize}>
            <Field>
              {t('ui.company_name')}<input data-bootstrap-company value={input.companyName} onChange={(event) => update('companyName', event.target.value)} />
            </Field>
            <Field>
              {t('ui.workspace_folder')}<WorkspaceRow>
                <input data-bootstrap-workspace readOnly value={workspace?.displayPath || workspace?.path || ''} title={workspace?.displayPath || workspace?.path} placeholder={t('ui.choose_a_local_folder_for_your_employees')} />
                <TestButton type="button" data-bootstrap-bind onClick={() => void handleBindWorkspace()} disabled={binding || initializing}>
                  <FolderOpenRoundedIcon fontSize="small" />
                  {binding ? t('ui.selecting') : workspace ? t('ui.change_folder') : t('ui.select_folder')}
                </TestButton>
              </WorkspaceRow>
              <WorkspaceHint>{t('ui.employees_work_in_this_folder_choose_an_empty_folder_or_an_existing')}</WorkspaceHint>
            </Field>
            {workspaceError && <StatusLine $error role="alert">{workspaceError}</StatusLine>}
            <SectionTitle>{t('ui.default_model')}</SectionTitle>
            <Field>
              {t('ui.model_name')}<input data-bootstrap-model value={input.modelName} onChange={(event) => update('modelName', event.target.value)} placeholder={t('ui.e_g_mimo_v2_5')} />
            </Field>
            <Field>
              {t('ui.api_endpoint')}<input data-bootstrap-base-url value={input.baseUrl} onChange={(event) => update('baseUrl', event.target.value)} placeholder="https://api.example.com/v1" />
            </Field>
            <KeyRow>
              <Field>
                API Key
                <PasswordWrap>
                  <input data-bootstrap-api-key type={showApiKey ? 'text' : 'password'} value={input.apiKey} onChange={(event) => update('apiKey', event.target.value)} />
                  <button type="button" aria-label={t('ui.show_or_hide_api_key')} onClick={() => setShowApiKey((value) => !value)}>
                    {showApiKey ? <VisibilityOffRoundedIcon fontSize="small" /> : <VisibilityRoundedIcon fontSize="small" />}
                  </button>
                </PasswordWrap>
              </Field>
              <TestButton type="button" data-bootstrap-test onClick={() => void handleTest()} disabled={!readyToTest || testing}>
                {testing ? t('ui.connecting') : t('ui.test_connection')}
              </TestButton>
            </KeyRow>
            {message && <StatusLine $error={!tested}>{tested && <CheckCircleRoundedIcon fontSize="small" />}{message}</StatusLine>}
            <EnterButton type="submit" data-bootstrap-enter disabled={!tested || !workspace || binding || initializing}>
              {initializing ? t('ui.opening') : t('ui.enter_office')}
            </EnterButton>
          </Form>
        </Content>
      </Card>
    </Overlay>
  )
}
