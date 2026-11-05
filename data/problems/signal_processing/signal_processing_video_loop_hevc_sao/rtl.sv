module signal_processing_video_loop_hevc_sao (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [2:0] in_mode,
    input logic [7:0] in_rec,in_a,in_b,in_c,in_f,
    input logic signed [7:0] in_offset0,in_offset1,in_offset2,in_offset3,in_offset4,
    input logic [255:0] in_band_offsets,
    input logic [7:0] in_tc_q,in_tc,
    input logic in_mask_q,
    output logic out_valid,
    input logic out_ready,
    output logic [7:0] out_rec,
    output logic signed [1:0] out_sign
);
    function automatic integer sign(input integer x);
        begin sign=x<0 ? -1 : x>0 ? 1 : 0;end
    endfunction
    function automatic integer clip(input integer x);
        begin clip=x<0 ? 0 : x>255 ? 255 : x;end
    endfunction
    logic occupied;
    integer rec,a,b,c,f,edge_type,offset,tc,prediction,delta,result,output_sign;
    assign in_ready=!occupied || out_ready;
    assign out_valid=occupied;
    always_comb begin
        rec=int'(in_rec);a=int'(in_a);b=int'(in_b);c=int'(in_c);f=int'(in_f);
        result=rec;output_sign=0;edge_type=0;offset=0;tc=0;prediction=0;delta=0;
        case(in_mode)
            0:output_sign=sign(rec-a);
            1,2,3,4:begin
                edge_type=sign(rec-a)+sign(rec-b)+2;
                case(edge_type)
                    0:offset=$signed(in_offset0);1:offset=$signed(in_offset1);
                    2:offset=$signed(in_offset2);3:offset=$signed(in_offset3);
                    default:offset=$signed(in_offset4);
                endcase
                result=clip(rec+offset);
            end
            5:begin offset=$signed(in_band_offsets[8*(rec>>3)+:8]);result=clip(rec+offset);end
            6:begin
                prediction=(f+2*a+2*rec+2*b+c+4)>>>3;tc=int'(in_tc_q);
                delta=prediction-rec;
                if(delta>tc) delta=tc;
                if(delta < -tc) delta=-tc;
                result=rec+delta;
            end
            7:begin
                delta=(4*(rec-a)+c-b+4)>>>3;tc=int'(in_tc);
                if(delta>tc) delta=tc;
                if(delta < -tc) delta=-tc;
                result=in_mask_q ? clip(rec-delta) : rec;
            end
        endcase
    end
    always_ff @(posedge clk) begin
        if(rst) begin occupied<=0;out_rec<=0;out_sign<=0;end
        else if(in_valid && in_ready) begin occupied<=1;out_rec<=result[7:0];out_sign<=output_sign[1:0];end
        else if(out_ready) occupied<=0;
    end
endmodule
