'use client'

import { createContext, useContext } from 'use-context-selector'
import useSWR from 'swr'
import { fetchWorkspaces, fetchWorkspacesHierarchy } from '@/service/common'
import type { IWorkspace } from '@/models/common'

export type WorkspaceTreeNode = {
  id: string
  name: string
  plan: string
  status: string
  created_at: number
  current: boolean
  children: WorkspaceTreeNode[]
}

export type WorkspacesContextValue = {
  workspaces: IWorkspace[]
  workspacesHierarchy: WorkspaceTreeNode[]
}

const WorkspacesContext = createContext<WorkspacesContextValue>({
  workspaces: [],
  workspacesHierarchy: [],
})

type IWorkspaceProviderProps = {
  children: React.ReactNode
}

export const WorkspaceProvider = ({
  children,
}: IWorkspaceProviderProps) => {
  const { data: flatData } = useSWR({ url: '/workspaces' }, fetchWorkspaces)
  const { data: hierarchyData } = useSWR({ url: '/workspaces/hierarchy' }, fetchWorkspacesHierarchy)

  // Convert flat workspaces to tree structure
  const buildWorkspaceTree = () => {
    const workspaces = flatData?.workspaces || []
    // Ensure hierarchy is always an array, even if API returns null
    const hierarchy = Array.isArray(hierarchyData?.hierarchy) ? hierarchyData.hierarchy : []

    // Create a set of accessible workspace IDs for quick lookup
    const accessibleWorkspaceIds = new Set(workspaces.map(workspace => workspace.id))

    // Create a map from workspace ID to workspace object
    const workspaceMap = new Map<string, IWorkspace>()
    workspaces.forEach((workspace) => {
      workspaceMap.set(workspace.id, workspace)
    })

    const buildTree = (nodes: any[]): WorkspaceTreeNode[] => {
      // Ensure nodes is always an array
      if (!Array.isArray(nodes)) return []
      
      return nodes.map((node) => {
        // Check if this workspace is accessible to the user
        if (!accessibleWorkspaceIds.has(node.id)) return null
        
        const workspace = workspaceMap.get(node.id)
        if (!workspace) return null

        // Recursively build children, filtering out inaccessible workspaces
        const children = buildTree(node.children || [])
        
        return {
          ...workspace,
          children,
        }
      }).filter(Boolean) as WorkspaceTreeNode[]
    }

    return buildTree(hierarchy)
  }

  const workspacesHierarchy = buildWorkspaceTree()

  return (
    <WorkspacesContext.Provider value={{
      workspaces: flatData?.workspaces || [],
      workspacesHierarchy,
    }}>
      {children}
    </WorkspacesContext.Provider>
  )
}

export const useWorkspacesContext = () => useContext(WorkspacesContext)

export default WorkspacesContext
