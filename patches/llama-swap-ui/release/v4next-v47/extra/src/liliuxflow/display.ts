type Text=(key:string,params?:Record<string,unknown>)=>string;
const labels:Record<string,Record<string,string>>={
 state:{ready:"model.ready",starting:"model.starting",stopping:"model.stopping",stopped:"model.stopped"},
 tab:{chat:"manager.tab.chat",images:"manager.tab.images",speech:"manager.tab.speech",audio:"manager.tab.audio",rerank:"manager.tab.rerank",concurrency:"manager.tab.concurrency"},
 theme:{default:"manager.theme.default",ocean:"manager.theme.ocean",violet:"manager.theme.violet",emerald:"manager.theme.emerald",rose:"manager.theme.rose",amber:"manager.theme.amber",slate:"manager.theme.slate",sunset:"manager.theme.sunset",terminal:"manager.theme.terminal",mc:"manager.theme.mc",solarized:"manager.theme.solarized"},
 mode:{light:"theme.light",dark:"theme.dark",system:"theme.system"},
 column:{id:"manager.column.id",time:"manager.column.time",src:"manager.column.source",model:"manager.column.model",req_path:"manager.column.path",resp_status_code:"manager.column.status",resp_content_type:"manager.column.contentType",cached:"manager.column.cached",prompt:"manager.column.prompt",generated:"manager.column.generated",drafted:"manager.column.drafted",prompt_speed:"manager.column.prefill",gen_speed:"manager.column.decode",duration:"manager.column.duration",capture:"manager.column.capture",meta:"manager.column.meta",cancel:"manager.column.cancel",elapsed:"manager.column.elapsed",request:"manager.column.request",identity:"manager.column.address",user_agent:"manager.column.userAgent",session_id:"manager.column.session",bytes_received:"manager.column.received"},
  connection:{connected:"manager.connection.connected",connecting:"manager.connection.connecting",disconnected:"manager.connection.disconnected",unknown:"manager.connection.unknown"},
  capability:{vision:"manager.capability.vision",audio_transcriptions:"manager.capability.audioTranscription",audio_speech:"manager.capability.audioSpeech",image_generation:"manager.capability.imageGeneration",image_to_image:"manager.capability.imageToImage",function_calling:"manager.capability.functionCalling",reranker:"manager.capability.reranker"},
  concurrencySection:{selectors:"manager.concurrency.selectors",models:"navigation.models",peers:"manager.concurrency.peers"},
  environment:{native:"manager.hardware.environment.native"},
  accelerator:{gpu:"manager.hardware.accelerator.gpu",npu:"manager.hardware.accelerator.npu",other:"manager.hardware.accelerator.other"},
  memoryKind:{dedicated:"manager.hardware.memory.dedicated",unified:"manager.hardware.memory.unified",shared_system:"manager.hardware.memory.sharedSystem",unknown:"manager.hardware.memory.unknown"},
};
/** Translate the presentation of fixed enum codes without altering the data,
 * selected value, profile store, callbacks or API request. */
export function displayEnum(t:Text,domain:string,value:string,fallback?:(value:string)=>string):string{
 const group=Object.hasOwn(labels,domain) ? labels[domain] : undefined;
 const key=group && Object.hasOwn(group,value) ? group[value] : undefined;
 return key ? t(key) : fallback ? fallback(value) : value;
}
