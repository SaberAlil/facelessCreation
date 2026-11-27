import { JobState } from '../api';

/** Plain-language status for a video. */
export function stateLabel(state: JobState): { text: string; cls: string } {
  switch (state) {
    case 'DRAFT': return { text: 'Writing the script', cls: 'working' };
    case 'RESEARCH_REVIEW': return { text: 'Check the script', cls: 'review' };
    case 'SCRIPT_APPROVED': return { text: 'Pictures', cls: 'working' };
    case 'ASSETS_READY': return { text: 'Making the video', cls: 'working' };
    case 'RENDERED': return { text: 'Watch & approve', cls: 'review' };
    case 'FINAL_APPROVED': return { text: 'Preparing downloads', cls: 'working' };
    case 'EXPORTED': return { text: 'Ready to publish', cls: 'ok' };
    case 'FAILED': return { text: 'Needs attention', cls: 'bad' };
  }
}
