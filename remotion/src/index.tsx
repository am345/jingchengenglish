import React from 'react';
import {AbsoluteFill, Composition, Img, registerRoot, staticFile, useCurrentFrame} from 'remotion';

type Row={word:string;meaning:string;active_ranges?:[number,number][]};
type Clip={id:string;start_frame:number;end_frame:number;teacher_prefix?:string;source_start_frame?:number;
  board:{heading:string;rows:Row[];note?:string}};
type Lesson={id:string;title:string;subtitle?:string;fps:number;width:number;height:number;duration_frames:number;
  teacher?:{left:number;top:number;scale:number};font_file?:string;clips:Clip[]};
const demo:Lesson={id:'demo',title:'景程口语',fps:24,width:1280,height:720,duration_frames:72,
  clips:[{id:'G01',start_frame:0,end_frame:72,board:{heading:'What is he like?',rows:[{word:'kind',meaning:'友善的'}]}}]};

const Film:React.FC<Lesson>=(p)=>{
  const f=useCurrentFrame();
  const active=p.clips.find(c=>f>=c.start_frame&&f<c.end_frame);
  const c=active??[...p.clips].reverse().find(c=>f>=c.start_frame)??p.clips[0];
  const local=f-c.start_frame;
  const t=p.teacher??{left:0,top:0,scale:1};
  const full=!active?.teacher_prefix;
  return <AbsoluteFill style={{background:'#173f35',color:'#faf6e8',fontFamily:p.font_file?'LessonFont, sans-serif':'Arial, sans-serif'}}>
    {p.font_file&&<style>{`@font-face{font-family:LessonFont;src:url('${staticFile(p.font_file)}')}`}</style>}
    <div style={{position:'absolute',inset:24,border:'2px solid #7e9d83',borderRadius:16}}/>
    <div style={{position:'absolute',left:52,top:45,width:full?1100:680}}>
      <div style={{fontSize:20,letterSpacing:2,color:'#c2cfb6'}}>{p.subtitle??'跟着老师学 · 理解与表达'}</div>
      <div style={{fontSize:43,lineHeight:1.15,fontWeight:700,marginTop:28,marginBottom:30}}>{c.board.heading}</div>
      {c.board.rows.map((r,i)=>{const on=r.active_ranges?.some(([a,b])=>local>=a&&local<b);return <div key={i}
        style={{display:'flex',alignItems:'baseline',gap:24,height:84,color:on?'#f6d56b':'#faf6e8'}}>
        <span style={{fontSize:50,fontWeight:700,minWidth:320}}>{r.word}</span>
        <span style={{fontSize:30}}>{r.meaning}</span>
      </div>})}
      {c.board.note&&<div style={{fontSize:26,color:'#c2cfb6',marginTop:20,lineHeight:1.4}}>{c.board.note}</div>}
    </div>
    {active?.teacher_prefix&&<Img src={staticFile(`${active.teacher_prefix}/${String(f-active.start_frame+(active.source_start_frame??0)).padStart(4,'0')}.png`)}
      style={{position:'absolute',width:1280,height:720,left:t.left,top:t.top,transform:`scale(${t.scale})`,transformOrigin:'top left',objectFit:'contain'}}/>}
    <div style={{position:'absolute',right:48,top:42,fontSize:24,fontWeight:700,color:'#f4d275'}}>{p.title}</div>
  </AbsoluteFill>;
};
const Root=()=> <Composition id="Lesson" component={Film} width={1280} height={720} fps={24} durationInFrames={72}
  defaultProps={demo} calculateMetadata={({props})=>({durationInFrames:props.duration_frames,fps:props.fps,width:props.width,height:props.height})}/>;
registerRoot(Root);
