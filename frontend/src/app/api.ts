import { HttpClient, HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
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

export interface StageRun {
  id: number; stage: string; status: string; started: string; duration_s: number;
  device: string | null; peak_rss_mib: number | null; peak_children_rss_mib: number | null;
  peak_vram_used_mib: number | null; fallbacks: string[]; error: string | null;
  details: Record<string, unknown>;
}

export interface JobDetail {
  job: JobRow; title: string | null;
  gate: { state: JobState; reason: string; next: string; done: boolean };
  fresh: Record<string, boolean>; approvals: Record<string, boolean>;
  runs: StageRun[]; files: Record<string, boolean>;
  timing: { duration: number; provider: string; demo: boolean } | null;
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

export interface SceneAsset {
  scene_id: string; kind: string; library_id: string | null; path: string; template: string | null;
  source_url: string; creator: string; license: string; attribution: string;
  commercial_use: boolean; provider: string; ai_generated: boolean; rights: string; reason: string;
}
export interface AssetsView {
  manifest: { scenes: SceneAsset[]; music: SceneAsset | null };
  problems: string[]; fresh: boolean; approved: boolean;
}

export interface Caption { index: number; start: number; end: number; text: string }
export interface CaptionsView {
  track: { source: string; model: string; approximate: boolean; reviewed: boolean;
           divergence_ratio: number | null; divergences: string[]; captions: Caption[] } | null;
  fresh: boolean;
}

export interface CheckItem { check: string; ok: boolean; detail: string; required: boolean }
export interface ReviewView { title: string | null; checklist: CheckItem[]; approved: boolean }

export interface Task {
  id: string; job_id: string | null; action: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  created: string; started: string | null; finished: string | null;
  error: string | null; hint: string | null; result: unknown;
}

export interface CalendarRow {
  day: number; date: string | null; topic: string; original_topic: string;
  source_row: number; job: { id: string; state: JobState } | null;
}

export interface Approval { confirm: string; reviewer: string; notes?: string; acknowledge_flags?: boolean }

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  private p<T>(o: Observable<T>): Promise<T> { return firstValueFrom(o); }

  system() { return this.p(this.http.get<Record<string, any>>('/api/system')); }
  doctor(network = false) {
    return this.p(this.http.get<any[]>(`/api/doctor?network=${network}`));
  }
  calendar(from = 1, to = 400) {
    return this.p(this.http.get<CalendarRow[]>(`/api/calendar?from_day=${from}&to_day=${to}`));
  }
  importCalendar(file: File, replace: boolean) {
    const fd = new FormData();
    fd.append('file', file);
    return this.p(this.http.post<{ rows: number }>(`/api/calendar/import?replace=${replace}`, fd));
  }
  jobs() { return this.p(this.http.get<JobRow[]>('/api/jobs')); }
  createJob(body: { topic?: string; day?: number }) {
    return this.p(this.http.post<{ id: string }>('/api/jobs', body));
  }
  job(id: string) { return this.p(this.http.get<JobDetail>(`/api/jobs/${id}`)); }
  run(id: string, stage: string) {
    return this.p(this.http.post<Task>(`/api/jobs/${id}/run/${stage}`, {}));
  }
  draft(id: string, body: { llm: boolean; text?: string; title?: string }) {
    return this.p(this.http.post<Task>(`/api/jobs/${id}/draft`, body));
  }
  task(taskId: string) { return this.p(this.http.get<Task>(`/api/tasks/${taskId}`)); }
  tasks(jobId: string) { return this.p(this.http.get<Task[]>(`/api/tasks?job_id=${jobId}`)); }

  script(id: string) { return this.p(this.http.get<ScriptView>(`/api/jobs/${id}/script`)); }
  saveScript(id: string, s: Script) {
    return this.p(this.http.put<ScriptView>(`/api/jobs/${id}/script`, s));
  }
  research(id: string) {
    return this.p(this.http.get<{ research: any; problems: string[] }>(`/api/jobs/${id}/research`));
  }
  approveScript(id: string, a: Approval) {
    return this.p(this.http.post(`/api/jobs/${id}/approve-script`, a));
  }

  assets(id: string) { return this.p(this.http.get<AssetsView>(`/api/jobs/${id}/assets`)); }
  rights(id: string, status: string, scene_ids: string[] | null) {
    return this.p(this.http.post<AssetsView>(`/api/jobs/${id}/assets/rights`, { status, scene_ids }));
  }
  replaceAsset(id: string, body: { scene_id: string; procedural?: boolean; overlay?: string; library_id?: string }) {
    return this.p(this.http.post<AssetsView>(`/api/jobs/${id}/assets/replace`, body));
  }
  approveAssets(id: string, a: Approval) {
    return this.p(this.http.post(`/api/jobs/${id}/approve-assets`, a));
  }

  captions(id: string) { return this.p(this.http.get<CaptionsView>(`/api/jobs/${id}/captions`)); }
  saveCaptions(id: string, captions: Caption[]) {
    return this.p(this.http.put<CaptionsView>(`/api/jobs/${id}/captions`, { captions }));
  }
  captionsReviewed(id: string) {
    return this.p(this.http.post<CaptionsView>(`/api/jobs/${id}/captions/mark-reviewed`, {}));
  }

  review(id: string) { return this.p(this.http.get<ReviewView>(`/api/jobs/${id}/review`)); }
  approveFinal(id: string, a: Approval) {
    return this.p(this.http.post(`/api/jobs/${id}/approve-final`, a));
  }
  fileUrl(id: string, name: string, bust = '') {
    return `/api/jobs/${id}/files/${name}${bust ? '?v=' + bust : ''}`;
  }
}
