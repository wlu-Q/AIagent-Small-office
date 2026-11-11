import { t, useLocale } from '../i18n'
import React, { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import Fab from '@mui/material/Fab'
import Button from '@mui/material/Button'
import IconButton from '@mui/material/IconButton'
import Tooltip from '@mui/material/Tooltip'
import FolderOpenRoundedIcon from '@mui/icons-material/FolderOpenRounded'
import CloseIcon from '@mui/icons-material/Close'

import phaserGame from '../PhaserGame'
import {
  initializeWorkspaceBridge,
  refreshWorkspaceSummary,
  selectAndBindWorkspace,
  subscribeWorkspaceSummary,
} from '../services/desktop/workspaceBridge'
import type { WorkspaceSummary, WorkspaceTreeNode } from '../services/desktop/types'
import Game from '../scenes/Game'
import { useDesktopGatewayStore } from '../state/desktopGatewayStore'
import { pushToast } from '../state/toastStore'
import { useWorkspaceStore } from '../state/workspaceStore'

const FloatingAction = styled.div`
  position: fixed;
  left: 88px;
  top: 84px;
  z-index: 12000;
`

const Overlay = styled.div`
  position: fixed;
  inset: 0;
  background:
    radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 32%),
    rgba(8, 11, 20, 0.56);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 48px 16px 16px;
  z-index: 20000;
`

const Panel = styled.div`
  width: min(760px, calc(100vw - 32px));
  max-height: calc(100vh - 64px);
  overflow: auto;
  background:
    linear-gradient(180deg, rgba(32, 39, 58, 0.98), rgba(18, 23, 38, 0.98)),
    #20273a;
  border: 1px solid rgba(56, 189, 248, 0.2);
  border-radius: 24px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.42);
  padding: 24px;
  color: #eef3ff;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;

  h2 {
    margin: 0;
    font-size: 26px;
  }
`

const Header = styled.div`
  padding-right: 36px;

  p {
    margin: 8px 0 0;
    color: #9fb0cf;
    line-height: 1.6;
  }
`

const StatusCard = styled.div`
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(10, 14, 24, 0.32);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
`

const StatusTitle = styled.div`
  font-size: 14px;
  color: #9fb0cf;
`

const StatusValue = styled.div<{ $tone: string }>`
  font-size: 18px;
  font-weight: 700;
  color: ${(props) => props.$tone};
`

const InfoGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;

  @media (max-width: 760px) {
    grid-template-columns: 1fr;
  }
`

const InfoCard = styled.div`
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(10, 14, 24, 0.32);
  padding: 16px 18px;

  h3 {
    margin: 0 0 10px;
    font-size: 16px;
  }

  p {
    margin: 0;
    color: #d8e1f7;
    line-height: 1.7;
    word-break: break-word;
  }
`

const TreeCard = styled(InfoCard)`
  display: flex;
  flex-direction: column;
  gap: 14px;
`

const TreeHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
`

const TreeToggle = styled.button`
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(15, 23, 42, 0.62);
  color: #dbeafe;
  border-radius: 999px;
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;

  &:hover {
    border-color: rgba(56, 189, 248, 0.5);
    color: #c8f1ff;
  }
`

const TreeContainer = styled.div`
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(6, 10, 18, 0.44);
  padding: 12px;
  max-height: 320px;
  overflow: auto;
`

const TreeNodeButton = styled.button<{ $depth: number; $muted?: boolean }>`
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px 8px calc(${(props) => props.$depth * 18}px + 8px);
  border: 0;
  background: transparent;
  color: ${(props) => (props.$muted ? '#8ea1c4' : '#e7eefc')};
  text-align: left;
  border-radius: 12px;
  cursor: ${(props) => (props.$muted ? 'default' : 'pointer')};
  font-size: 14px;

  &:hover {
    background: ${(props) => (props.$muted ? 'transparent' : 'rgba(59, 130, 246, 0.12)')};
  }
`

const TreeChildren = styled.div`
  display: flex;
  flex-direction: column;
`

const TreeIcon = styled.span`
  width: 18px;
  text-align: center;
  flex-shrink: 0;
  color: #8fb5ff;
`

const TreeName = styled.span`
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`

const Footer = styled.div`
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  flex-wrap: wrap;
`

function getStatusTone(status: string) {
  switch (status) {
    case 'ready':
      return '#67e8f9'
    case 'binding':
      return '#93c5fd'
    case 'error':
      return '#fca5a5'
    case 'browser':
      return '#fcd34d'
    default:
      return '#e2e8f0'
  }
}

function getStatusLabel(status: string) {
  switch (status) {
    case 'ready':
      return t('ui.project_bound')
    case 'binding':
      return t('ui.binding')
    case 'error':
      return t('ui.binding_failed')
    case 'browser':
      return t('ui.browser_mode')
    default:
      return t('ui.not_bound')
  }
}

function WorkspaceTreeItem({ node, depth }: { node: WorkspaceTreeNode; depth: number }) {
  useLocale()
  const [open, setOpen] = useState(false)
  const hasChildren = node.type === 'directory' && Boolean(node.children?.length)

  if (node.type === 'more') {
    return (
      <TreeNodeButton type="button" $depth={depth} $muted>
        <TreeIcon>…</TreeIcon>
        <TreeName>{node.name}</TreeName>
      </TreeNodeButton>
    )
  }

  return (
    <div>
      <TreeNodeButton
        type="button"
        $depth={depth}
        onClick={() => {
          if (hasChildren) {
            setOpen((value) => !value)
          }
        }}
      >
        <TreeIcon>
          {hasChildren ? (open ? '⌄' : '›') : node.type === 'directory' ? '📁' : '📄'}
        </TreeIcon>
        <TreeName>{node.name}</TreeName>
      </TreeNodeButton>

      {hasChildren && open && (
        <TreeChildren>
          {node.children!.map((child) => (
            <WorkspaceTreeItem key={`${depth}-${node.name}-${child.name}`} node={child} depth={depth + 1} />
          ))}
        </TreeChildren>
      )}
    </div>
  )
}

export default function WorkspacePanel() {
  useLocale()
  const [open, setOpen] = useState(false)
  const [treeOpen, setTreeOpen] = useState(false)
  const gatewayStatus = useDesktopGatewayStore((state) => state.status)
  const workspaceStatus = useWorkspaceStore((state) => state.status)
  const workspaceDetail = useWorkspaceStore((state) => state.detail)
  const [workspaceSummary, setWorkspaceSummary] = useState<WorkspaceSummary | undefined>(undefined)

  useEffect(() => {
    const unsubscribeSummary = subscribeWorkspaceSummary(setWorkspaceSummary)
    const cleanupBridge = initializeWorkspaceBridge()

    return () => {
      unsubscribeSummary()
      cleanupBridge()
    }
  }, [])

  useEffect(() => {
    const game = phaserGame.scene.keys.game as Game | undefined

    if (open) {
      game?.disableKeys()
    } else {
      game?.enableKeys()
    }

    return () => {
      game?.enableKeys()
    }
  }, [open])

  useEffect(() => {
    if (!workspaceSummary) {
      setTreeOpen(false)
    }
  }, [workspaceSummary])

  useEffect(() => {
    if (!open) {
      return
    }

    void refreshWorkspaceSummary()
      .then(setWorkspaceSummary)
      .catch((error) => {
        pushToast(error instanceof Error ? error.message : t('ui.unable_to_load_current_project_status'), 'error')
      })
  }, [open])

  const handleBind = async () => {
    try {
      const workspace = await selectAndBindWorkspace()
      if (!workspace) {
        return
      }

      setWorkspaceSummary(workspace)
      pushToast(t('ui.local_project_bound'), 'success')
    } catch (error) {
      pushToast(error instanceof Error ? error.message : t('ui.unable_to_bind_local_project'), 'error')
    }
  }

  return (
    <>
      <FloatingAction>
        <Tooltip title={t('ui.local_project')}>
          <Fab color="primary" onClick={() => setOpen(true)}>
            <FolderOpenRoundedIcon />
          </Fab>
        </Tooltip>
      </FloatingAction>

      {open && (
        <Overlay onMouseDown={() => setOpen(false)}>
          <Panel onMouseDown={(event) => event.stopPropagation()}>
            <IconButton
              aria-label={t('ui.close_local_project_panel')}
              onClick={() => setOpen(false)}
              size="small"
              style={{ position: 'absolute', top: 18, right: 18, color: '#dbeafe' }}
            >
              <CloseIcon />
            </IconButton>

            <Header>
              <h2>{t('ui.local_project_workspace')}</h2>
              <p>{t('ui.choose_a_local_project_folder_this_panel_shows_its_binding_status_and')}</p>
            </Header>

            <StatusCard>
              <StatusTitle>{t('ui.desktop_service_status')}</StatusTitle>
              <StatusValue $tone={getStatusTone(workspaceStatus)}>
                {getStatusLabel(workspaceStatus)} · {gatewayStatus.lifecycle}
              </StatusValue>
              <p style={{ margin: 0, color: '#d8e1f7', lineHeight: 1.7 }}>{workspaceDetail}</p>
            </StatusCard>

            <InfoGrid>
              <InfoCard>
                <h3>{t('ui.current_project')}</h3>
                <p>{workspaceSummary ? workspaceSummary.name : t('ui.no_local_project_bound')}</p>
              </InfoCard>
              <InfoCard style={{ gridColumn: '1 / -1' }}>
                <h3>{t('ui.project_path')}</h3>
                <p>{workspaceSummary
                  ? (workspaceSummary.displayPath ?? workspaceSummary.path)
                  : t('ui.click_bind_project_and_select_a_local_folder')}</p>
              </InfoCard>
            </InfoGrid>

            <TreeCard>
              <TreeHeader>
                <div>
                  <h3 style={{ marginBottom: 6 }}>{t('ui.project_directory')}</h3>
                  <p style={{ color: '#9fb0cf' }}>
                    {t('ui.expand_to_view_the_project_s_folders_and_main_files')}</p>
                </div>
                <TreeToggle
                  type="button"
                  onClick={() => setTreeOpen((value) => !value)}
                  disabled={!workspaceSummary}
                  style={{ opacity: workspaceSummary ? 1 : 0.6 }}
                >
                  {treeOpen ? t('ui.collapse_directory') : t('ui.view_directory')}
                </TreeToggle>
              </TreeHeader>

              {workspaceSummary ? (
                treeOpen ? (
                  <TreeContainer>
                    {workspaceSummary.tree.length > 0 ? (
                      workspaceSummary.tree.map((node) => (
                        <WorkspaceTreeItem key={`root-${node.name}`} node={node} depth={0} />
                      ))
                    ) : (
                      <p style={{ margin: 0, color: '#9fb0cf' }}>{t('ui.nothing_to_display_in_this_folder')}</p>
                    )}
                  </TreeContainer>
                ) : (
                  <p style={{ margin: 0, color: '#9fb0cf' }}>{t('ui.directory_collapsed_click_view_directory_to_expand')}</p>
                )
              ) : (
                <p style={{ margin: 0, color: '#9fb0cf' }}>{t('ui.bind_a_local_project_to_see_its_directory_here')}</p>
              )}
            </TreeCard>

            <Footer>
              <Button
                variant="outlined"
                onClick={() => setOpen(false)}
                style={{ color: '#dbeafe', borderColor: 'rgba(219,234,254,0.35)' }}
              >
                {t('ui.close')}</Button>
              <Button
                variant="contained"
                aria-label="select-local-project-directory"
                onClick={handleBind}
                disabled={gatewayStatus.mode !== 'electron' || workspaceStatus === 'binding'}
              >
                {workspaceSummary ? t('ui.choose_another_project') : t('ui.bind_project')}
              </Button>
            </Footer>
          </Panel>
        </Overlay>
      )}
    </>
  )
}
