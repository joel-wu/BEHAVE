module hebbian_rule (
    input logic clk, rst, start,
    input logic signed [3:0] a, b,
    input logic [1:0] gate_select,
    output logic signed [3:0] w1, w2, bias,
    output logic [3:0] present_state, next_state
);
    logic [1:0] sample_index, saved_gate;
    logic signed [3:0] xa, xb, target;
    logic signed [4:0] delta1, delta2;
    logic signed [3:0] delta_bias;
    always_comb begin
        case (present_state)
            0: next_state=start ? 4'd1 : 4'd0;
            2: next_state=4'd3+4'(saved_gate);
            3,4,5,6: next_state=7;
            9: next_state=sample_index==3 ? 4'd10 : 4'd1;
            10: next_state=0;
            default: next_state=present_state+1'b1;
        endcase
    end
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin
            present_state<=0; sample_index<=0; saved_gate<=0;
            w1<=0; w2<=0; bias<=0; xa<=0; xb<=0; target<=0;
            delta1<=0; delta2<=0; delta_bias<=0;
        end else begin
            present_state<=next_state;
            case (present_state)
                0: if (start) begin
                    w1<=0; w2<=0; bias<=0; sample_index<=0; saved_gate<=gate_select;
                end
                1: begin xa<=a; xb<=b; end
                3: target<=(xa==4'sd1 && xb==4'sd1) ? 4'sd1 : -4'sd1;
                4: target<=(xa==4'sd1 || xb==4'sd1) ? 4'sd1 : -4'sd1;
                5: target<=(xa==4'sd1 && xb==4'sd1) ? -4'sd1 : 4'sd1;
                6: target<=(xa==4'sd1 || xb==4'sd1) ? -4'sd1 : 4'sd1;
                7: begin delta1<=5'(xa)*5'(target); delta2<=5'(xb)*5'(target); delta_bias<=target; end
                8: begin w1<=4'(5'(w1)+delta1); w2<=4'(5'(w2)+delta2); bias<=bias+delta_bias; end
                9: if (sample_index!=3) sample_index<=sample_index+1'b1;
                default: ;
            endcase
        end
    end
endmodule
