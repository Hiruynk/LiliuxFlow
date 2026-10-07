import type {PhaseStats} from '../lib/types';
import type {PhaseKind,PhaseMetric} from '../lib/statsTooltips';
export type UiText=(key:string,values?:Record<string,unknown>)=>string;
/** Display-only counterpart of the pinned tooltip helper. The original numeric
 * statistics and exact/approximate branching stay unchanged. */
export function localizedPhaseTooltip(kind:PhaseKind,metric:PhaseMetric,phase:PhaseStats,t:UiText):string{
 const approxSpeed=phase.approxTokens||phase.approxTimings;
 const note=(key:string)=>' '+t('manager.stats.note.'+key);
 switch(metric){
  case 'tokens':
   return t('manager.stats.value.'+kind+'.tokens')+(kind==='prompt'?'':phase.approxTokens?note(kind==='generation'?'chunk':'split'):'');
  case 'time':
   if(kind==='prompt'||kind==='generation')return t('manager.stats.value.'+kind+'.time'+(phase.approxTimings?'.browser':''));
   return t('manager.stats.value.'+kind+'.time')+(phase.approxTimings?note('browser'):'');
  case 'speed':
   return t('manager.stats.value.'+kind+'.speed')+(kind==='prompt'?(phase.approxTimings?note('firstToken'):''):(approxSpeed?note('derived'):''));
  case 'perToken':return t('manager.stats.value.'+(kind==='prompt'?'prompt':'generation')+'.perToken');
 }
 return '';
}
