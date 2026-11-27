import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, firstValueFrom } from 'rxjs';

/** Every request carries X-Faceless: the API rejects mutating calls without it. */
export const facelessHeader: HttpInterceptorFn = (req, next) =>
  next(req.clone({ setHeaders: { 'X-Faceless': '1' } }));

export function errorText(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    const d = e.error?.detail;
    const hint = e.error?.hint ? ` — ${e.error.hint}` : '';
    if (typeof d === 'string') return d + hint;
    if (Array.isArray(d)) return d.map((x) => x.msg ?? JSON.stringify(x)).join('; ');
    if (e.status === 0 || e.status === 504) return 'The server is not running. Start the backend (see README).';
    return e.message;
  }
  return String(e);
}

export type JobState =
  | 'DRAFT' | 'RESEARCH_REVIEW' | 'SCRIPT_APPROVED' | 'ASSETS_READY'
  | 'RENDERED' | 'FINAL_APPROVED' | 'EXPORTED' | 'FAILED';

export interface JobRow {
  id: string; topic: string; day: number | null; date: string | null; state: JobState;
  failed_stage: string | null; error: string | null; created: string; updated: string;
}

export interface Step { key: string; title: string; status: 'todo' | 'working' | 'review' | 'done'; hint: string }

export interface Task {
  id: string; job_id: string | null; action: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  created: string; started: string | null; finished: string | null;
  error: string | null; hint: string | null; result: unknown;
  progress: { message: string; done: number; total: number } | null;
}

export interface JobDetail {
  job: JobRow; title: string | null;
  gate: { state: JobState; reason: string; next: string; done: boolean };
  approvals: Record<string, boolean>;
  files: Record<string, boolean>;
  timing: { duration: number; provider: string; demo: boolean } | null;
  options: { subtitles: string; voice: string };
  steps: Step[];
  task: Task | null;
}

export interface Scene {
  id: string; narration: string; visual: string; search_terms: string[];
  overlay: string; claim_ids: string[];
}
export interface ScriptClaim { id: string; text: string; source_ids: string[]; needs_review: boolean }
export interface Script {
  title: string; hook: string; narration: string; scenes: Scene[]; claims: ScriptClaim[];
  platform: Record<string, unknown>; provider: string; provider_meta: Record<string, unknown>;
}
export interface ScriptView {
  script: Script; approved: boolean;
  report: { words: number; est_seconds: number; errors: string[]; warnings: string[];
            flagged_claims: string[] };
  research_problems: string[];
}
export interface Source { id: string; url: string | null; title: string; publisher: string; published: string }

export interface SceneAsset {
  scene_id: string; kind: string; library_id: string | null; path: string; template: string | null;
  source_url: string; creator: string; license: string; attribution: string;
  commercial_use: boolean; provider: string; ai_generated: boolean; rights: string; reason: string;
  candidates: string[];
}
export interface AssetsView {
  manifest: { scenes: SceneAsset[]; music: SceneAsset | null };
  problems: string[]; fresh: boolean; approved: boolean;
}

export interface Caption { index: number; start: number; end: number; text: string }
export interface CaptionsView {
  track: { source: string; model: string; language: string; translated_by: string; burn: boolean;
           approximate: boolean; reviewed: boolean; divergence_ratio: number | null;
           divergences: string[]; captions: Caption[] } | null;
  fresh: boolean;
}

export interface CheckItem { check: string; ok: boolean; detail: string; required: boolean }
export interface ReviewView { title: string | null; checklist: CheckItem[]; approved: boolean }

export interface CalendarRow {
  day: number; date: string | null; topic: string; original_topic: string;
  source_row: number; job: { id: string; state: JobState } | null;
  extra: Record<string, unknown>;
}

export interface Overview {
  today: string;
  upcoming: { day: number; date: string; topic: string; job: { id: string; state: JobState } | null }[];
  review: { id: string; topic: string; day: number | null; date: string | null; step: string }[];
  in_progress: { id: string; topic: string; step: string; task: Task | null }[];
  ai_images: boolean; reviewer_set: boolean;
}

export interface AppSettings {
  reviewer: string; default_subtitles: string; languages: Record<string, string>;
  default_voice: string; voices: Record<string, string>;
  max_duration_s: number; min_duration_s: number; picture_sources: string[];
}

export interface Approval { reviewer?: string; notes?: string; acknowledge_flags?: boolean }
export interface ApprovalResult { approved: boolean; hash: string; task: Task | null }

export const LANGUAGES: Record<string, string> = {
  en: 'English', it: 'Italian', es: 'Spanish', fr: 'French', de: 'German',
  pt: 'Portuguese', none: 'No subtitles',
};

/** Kokoro voices (same list as the server). */
export const VOICES: Record<string, string> = {
  af_heart: 'Heart · warm American female (default)', af_bella: 'Bella · lively American female',
  af_nicole: 'Nicole · soft American female', am_michael: 'Michael · calm American male',
  am_fenrir: 'Fenrir · deep American male', am_puck: 'Puck · energetic American male',
  bf_emma: 'Emma · British female', bm_george: 'George · British male',
  bm_fable: 'Fable · young British male',
};

/** Same estimate as the server: Kokoro speaks ~2.2 words/s plus pauses between scenes. */
export function estimateSeconds(scenes: { narration: string }[]): number {
  const words = scenes.map((s) => s.narration.trim().split(/\s+/).filter(Boolean).length)
    .reduce((a, b) => a + b, 0);
  return words / 2.2 + 0.25 * Math.max(0, scenes.length - 1) + 0.7;
}

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  private p<T>(o: Observable<T>): Promise<T> { return firstValueFrom(o); }

  // home & settings
  overview() { return this.p(this.http.get<Overview>('/api/overview')); }
  voiceSampleUrl(voice: string) { return `/api/voices/${encodeURIComponent(voice)}/sample`; }
  settings() { return this.p(this.http.get<AppSettings>('/api/settings')); }
  saveSettings(body: { reviewer?: string; default_subtitles?: string; default_voice?: string }) {
    return this.p(this.http.put<AppSettings>('/api/settings', body));
  }
  system() { return this.p(this.http.get<Record<string, any>>('/api/system')); }
  doctor(network = false) { return this.p(this.http.get<any[]>(`/api/doctor?network=${network}`)); }

  // calendar & jobs
  calendar(from = 1, to = 400) {
    return this.p(this.http.get<CalendarRow[]>(`/api/calendar?from_day=${from}&to_day=${to}`));
  }
  importCalendar(file: File, replace: boolean) {
    const fd = new FormData();
    fd.append('file', file);
    return this.p(this.http.post<{ rows: number }>(`/api/calendar/import?replace=${replace}`, fd));
  }
  jobs() { return this.p(this.http.get<JobRow[]>('/api/jobs')); }
  /** Create the job if needed and run every automatic step until something needs you. */
  make(body: { day?: number; topic?: string; subtitles?: string }) {
    return this.p(this.http.post<Task>('/api/make', body));
  }
  job(id: string) { return this.p(this.http.get<JobDetail>(`/api/jobs/${id}`)); }
  run(id: string, stage: string) { return this.p(this.http.post<Task>(`/api/jobs/${id}/run/${stage}`, {})); }
  task(taskId: string) { return this.p(this.http.get<Task>(`/api/tasks/${taskId}`)); }
  setOptions(id: string, body: { subtitles?: string; voice?: string }) {
    return this.p(this.http.put<{ options: { subtitles: string; voice: string } }>(`/api/jobs/${id}/options`, body));
  }

  // script
  script(id: string) { return this.p(this.http.get<ScriptView>(`/api/jobs/${id}/script`)); }
  saveScript(id: string, s: Script) { return this.p(this.http.put<ScriptView>(`/api/jobs/${id}/script`, s)); }
  research(id: string) {
    return this.p(this.http.get<{ research: { sources: Source[] }; problems: string[] }>(`/api/jobs/${id}/research`));
  }
  /** Ask the local AI to write the script again from the sources. */
  draft(id: string) { return this.p(this.http.post<Task>(`/api/jobs/${id}/draft`, { llm: true })); }
  approveScript(id: string, a: Approval) {
    return this.p(this.http.post<ApprovalResult>(`/api/jobs/${id}/approve-script`, a));
  }

  // pictures
  assets(id: string) { return this.p(this.http.get<AssetsView>(`/api/jobs/${id}/assets`)); }
  picture(id: string, scene: string, action: 'photo' | 'ai') {
    return this.p(this.http.post<Task>(`/api/jobs/${id}/scenes/${scene}/picture`, { action }));
  }
  upload(id: string, scene: string, file: File) {
    const fd = new FormData();
    fd.append('file', file);
    return this.p(this.http.post<AssetsView>(`/api/jobs/${id}/scenes/${scene}/upload`, fd));
  }
  approveAssets(id: string, a: Approval) {
    return this.p(this.http.post<ApprovalResult>(`/api/jobs/${id}/approve-assets`, a));
  }

  // video
  captions(id: string) { return this.p(this.http.get<CaptionsView>(`/api/jobs/${id}/captions`)); }
  saveCaptions(id: string, captions: Caption[]) {
    return this.p(this.http.put<CaptionsView>(`/api/jobs/${id}/captions`, { captions }));
  }
  review(id: string) { return this.p(this.http.get<ReviewView>(`/api/jobs/${id}/review`)); }
  approveFinal(id: string, a: Approval) {
    return this.p(this.http.post<ApprovalResult>(`/api/jobs/${id}/approve-final`, a));
  }

  // publish
  exportFiles(id: string) {
    return this.p(this.http.get<{ files: { name: string; bytes: number }[]; texts: Record<string, string>; folder: string }>(`/api/jobs/${id}/export`));
  }
  exportUrl(id: string, name: string) { return `/api/jobs/${id}/export/${encodeURIComponent(name)}`; }
  fileUrl(id: string, name: string, bust = '') {
    return `/api/jobs/${id}/files/${name}${bust ? '?v=' + bust : ''}`;
  }
}
