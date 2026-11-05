// Time-shared framed FFT: wider signed accumulations, two normalization stages.
// This is not the upstream continuous-pipeline microarchitecture.
module FramedFft64Fixed (
    input  logic clk, rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [3:0] in_shift,
    input  logic [15:0] in_dr, in_di,
    output logic out_valid,
    input  logic out_ready,
    output logic out_ovf1, out_ovf2,
    output logic [5:0] out_addr,
    output logic [18:0] out_dor, out_doi
);
    typedef enum logic [2:0] {LOAD, FIRST, ROTATE, SECOND, EMIT} phase_t;
    phase_t phase;
    logic [3:0] frame_shift;
    logic [5:0] position;
    logic [2:0] group_index, bin_index, tap;
    logic overflow1, overflow2;
    logic signed [15:0] samples_re [0:63], samples_im [0:63];
    logic signed [17:0] first_re [0:63], first_im [0:63];
    logic signed [17:0] rotated_re [0:63], rotated_im [0:63];
    logic signed [18:0] results_re [0:63], results_im [0:63];
    logic signed [63:0] accumulator_re, accumulator_im;
    logic signed [63:0] operand_re, operand_im, cosine, sine;
    logic signed [63:0] total_re, total_im, normalized_re, normalized_im;
    integer angle, address;

    function automatic signed [63:0] quadrant_cos(input integer a);
        case (a)
            0: quadrant_cos=32767;  1: quadrant_cos=32610;
            2: quadrant_cos=32138;  3: quadrant_cos=31357;
            4: quadrant_cos=30274;  5: quadrant_cos=28899;
            6: quadrant_cos=27246;  7: quadrant_cos=25330;
            8: quadrant_cos=23170;  9: quadrant_cos=20788;
           10: quadrant_cos=18205; 11: quadrant_cos=15447;
           12: quadrant_cos=12540; 13: quadrant_cos=9512;
           14: quadrant_cos=6393;  15: quadrant_cos=3212;
           default: quadrant_cos=0;
        endcase
    endfunction

    function automatic signed [63:0] cos64(input integer a);
        integer quadrant, offset;
        begin
            quadrant = (a & 63) >> 4;
            offset = a & 15;
            case (quadrant)
                0: cos64=quadrant_cos(offset);
                1: cos64=-quadrant_cos(16-offset);
                2: cos64=-quadrant_cos(offset);
                3: cos64=quadrant_cos(16-offset);
            endcase
        end
    endfunction

    always_comb begin
        operand_re=0; operand_im=0; angle=0; address=0;
        if (phase==FIRST) begin
            address=int'(group_index)+8*int'(tap);
            operand_re=64'($signed(samples_re[address]));
            operand_im=64'($signed(samples_im[address]));
            angle=8*int'(bin_index)*int'(tap);
        end else if (phase==ROTATE) begin
            operand_re=64'($signed(first_re[position]));
            operand_im=64'($signed(first_im[position]));
            angle=int'(position[5:3])*int'(position[2:0]);
        end else if (phase==SECOND) begin
            address=8*int'(tap)+int'(group_index);
            operand_re=64'($signed(rotated_re[address]));
            operand_im=64'($signed(rotated_im[address]));
            angle=8*int'(bin_index)*int'(tap);
        end
        cosine=cos64(angle);
        sine=cos64(16-angle);
        total_re=operand_re*cosine+operand_im*sine;
        total_im=operand_im*cosine-operand_re*sine;
        if (phase==ROTATE) begin
            normalized_re=total_re >>> 16;
            normalized_im=total_im >>> 16;
        end else begin
            total_re=total_re+accumulator_re;
            total_im=total_im+accumulator_im;
            normalized_re=total_re >>> 15;
            normalized_im=total_im >>> 15;
            if (phase==FIRST) begin
                normalized_re=(normalized_re <<< frame_shift[1:0]) >>> 1;
                normalized_im=(normalized_im <<< frame_shift[1:0]) >>> 1;
            end else begin
                normalized_re=(normalized_re <<< frame_shift[3:2]) >>> 1;
                normalized_im=(normalized_im <<< frame_shift[3:2]) >>> 1;
            end
        end
        in_ready=(phase==LOAD) && !rst;
        out_valid=(phase==EMIT) && !rst;
        out_addr=out_valid ? position : 6'd0;
        out_dor=out_valid ? results_re[position] : 19'd0;
        out_doi=out_valid ? results_im[position] : 19'd0;
        out_ovf1=out_valid && overflow1;
        out_ovf2=out_valid && overflow2;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            phase<=LOAD; position<=0; frame_shift<=0;
            group_index<=0; bin_index<=0; tap<=0;
            accumulator_re<=0; accumulator_im<=0;
            overflow1<=0; overflow2<=0;
        end else begin
            case (phase)
                LOAD: if (in_valid && in_ready) begin
                    samples_re[position]<=in_dr;
                    samples_im[position]<=in_di;
                    if (position==0) begin
                        frame_shift<=in_shift;
                        overflow1<=0; overflow2<=0;
                    end
                    if (position==63) begin
                        phase<=FIRST; position<=0;
                        group_index<=0; bin_index<=0; tap<=0;
                        accumulator_re<=0; accumulator_im<=0;
                    end else position<=position+1'b1;
                end
                FIRST, SECOND: begin
                    if (tap!=7) begin
                        accumulator_re<=total_re; accumulator_im<=total_im;
                        tap<=tap+1'b1;
                    end else begin
                        accumulator_re<=0; accumulator_im<=0; tap<=0;
                        if (phase==FIRST) begin
                            first_re[{group_index,bin_index}]<=normalized_re[17:0];
                            first_im[{group_index,bin_index}]<=normalized_im[17:0];
                            overflow1<=overflow1 || normalized_re < -64'sd131072 || normalized_re > 64'sd131071
                                                  || normalized_im < -64'sd131072 || normalized_im > 64'sd131071;
                        end else begin
                            results_re[{bin_index,group_index}]<=normalized_re[18:0];
                            results_im[{bin_index,group_index}]<=normalized_im[18:0];
                            overflow2<=overflow2 || normalized_re < -64'sd262144 || normalized_re > 64'sd262143
                                                  || normalized_im < -64'sd262144 || normalized_im > 64'sd262143;
                        end
                        if (bin_index!=7) bin_index<=bin_index+1'b1;
                        else begin
                            bin_index<=0;
                            if (group_index!=7) group_index<=group_index+1'b1;
                            else begin
                                group_index<=0; position<=0;
                                phase<=(phase==FIRST) ? ROTATE : EMIT;
                            end
                        end
                    end
                end
                ROTATE: begin
                    rotated_re[position]<=normalized_re[17:0];
                    rotated_im[position]<=normalized_im[17:0];
                    if (position==63) begin
                        position<=0; phase<=SECOND;
                        group_index<=0; bin_index<=0; tap<=0;
                    end else position<=position+1'b1;
                end
                EMIT: if (out_ready) begin
                    if (position==63) begin
                        phase<=LOAD; position<=0;
                    end else position<=position+1'b1;
                end
                default: phase<=LOAD;
            endcase
        end
    end
endmodule
