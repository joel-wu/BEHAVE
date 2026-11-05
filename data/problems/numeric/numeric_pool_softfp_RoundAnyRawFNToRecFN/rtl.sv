module RawFloatToRecFNRounder (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_invalidExc, in_infiniteExc, in_isNaN, in_isInf, in_isZero,
    input logic in_sign,
    input logic signed [9:0] in_sExp,
    input logic [26:0] in_sig,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [32:0] out_out,
    output logic [4:0] out_exceptionFlags
);
function automatic [64:0] quantize(input logic [26:0] sig, input integer shift,
                                 input logic [2:0] mode, input logic signbit);
    logic [63:0] q,rembits,half;
    logic inc;
    begin
        q=0; rembits=0; half=64'd1<<27;
        if(shift<=0) q={37'b0,sig}<<(-shift);
        else if(shift<=27) begin
            q=sig>>shift;rembits=sig&((64'd1<<shift)-1);half=64'd1<<(shift-1);
        end else rembits=sig;
        inc=0;
        case(mode)
            0:inc=(rembits>half)||((rembits==half)&&q[0]);
            4:inc=rembits>=half;
            2:inc=signbit&&(rembits!=0);
            3:inc=!signbit&&(rembits!=0);
            default:inc=0;
        endcase
        if(inc) q=q+1;
        if(mode==6 && rembits!=0) q=q|64'd1;
        quantize={rembits!=0,q};
    end
endfunction

logic [32:0] result;
logic [4:0] flags;
integer top,e,ue,shift,high,b;
logic [64:0] nr,qr;
logic [63:0] q;
logic tiny;
always_comb begin
    result={in_sign,32'b0};flags={in_invalidExc,in_infiniteExc,3'b0};
    top=0;e=0;ue=0;shift=0;high=0;nr=0;qr=0;q=0;tiny=0;
    if(in_invalidExc || in_isNaN) result=33'he0400000;
    else if(in_infiniteExc || in_isInf) result=result|33'hc0000000;
    else if(!in_isZero && in_sig!=0) begin
        for(b=0;b<27;b=b+1) if(in_sig>=(27'd1<<b)) top=b;
        e=$signed(in_sExp)-281+top;
        nr=quantize(in_sig,top-23,in_roundingMode,in_sign);
        ue=e+(nr[63:0]>=(64'd1<<24));
        if(ue>127) begin
            flags=flags|5;
            if(in_roundingMode==0 || in_roundingMode==4 ||
               (in_roundingMode==2 && in_sign) || (in_roundingMode==3 && !in_sign))
                result=result|33'hc0000000;
            else result=result|33'hbfffffff;
        end else begin
            shift=(e < -126) ? 132-$signed(in_sExp) : top-23;
            qr=quantize(in_sig,shift,in_roundingMode,in_sign);q=qr[63:0];
            tiny=in_detectTininess ? ue < -126 : e < -126;
            if(qr[64]) flags=flags|(tiny ? 5'd3 : 5'd1);
            if(e>=-126) begin
                if(q>=(64'd1<<24)) begin q=q>>1;e=e+1;end
                result=result|((33'(e+256))<<23)|(q&23'h7fffff);
            end else if(q!=0) begin
                for(b=0;b<24;b=b+1) if(q>=(64'd1<<b)) high=b;
                result=result|(33'(high+107)<<23)|((q<<(23-high))&23'h7fffff);
            end
        end
    end
end
assign in_ready=!out_valid || out_ready;
always_ff @(posedge clk) begin
    if(rst) begin out_valid<=0;out_out<=0;out_exceptionFlags<=0;end
    else if(in_ready) begin
        out_valid<=in_valid;
        out_out<=in_valid ? result : 0;
        out_exceptionFlags<=in_valid ? flags : 0;
    end
end
endmodule
