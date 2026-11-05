module gcd_top #(parameter int WIDTH = 4) (
    input logic clk, rst,
    input logic [WIDTH-1:0] A, B,
    input logic go,
    output logic [WIDTH-1:0] OUT,
    output logic done
);
    logic [1:0] state;
    logic equal, greater_than;
    gcd_controlpath #(.WIDTH(WIDTH)) controlpath (.clk, .rst, .go, .equal, .greater_than,
        .controlpath_state(state), .done);
    gcd_datapath #(.WIDTH(WIDTH)) datapath (.clk, .rst, .go, .A, .B,
        .controlpath_state(state), .OUT, .equal, .greater_than);
endmodule

module gcd_controlpath #(parameter int WIDTH = 4) (
    input logic clk,rst,go,equal,greater_than,
    output logic [1:0] controlpath_state,
    output logic done
);
    localparam logic [1:0] S0=0,S1=1,S2=2,S3=3;
    always_ff @(posedge clk) begin
        if (rst) begin controlpath_state<=S0; done<=0; end
        else begin
            done<=0;
            case(controlpath_state)
                S0: if(go) controlpath_state<=equal ? S1 : greater_than ? S2 : S3;
                S1: begin done<=1; controlpath_state<=S0; end
                S2,S3: controlpath_state<=equal ? S1 : greater_than ? S2 : S3;
                default: controlpath_state<=S0;
            endcase
        end
    end
endmodule

module gcd_datapath #(parameter int WIDTH = 4) (
    input logic clk,rst,go,
    input logic [WIDTH-1:0] A,B,
    input logic [1:0] controlpath_state,
    output logic [WIDTH-1:0] OUT,
    output logic equal,greater_than
);
    logic [WIDTH-1:0] A_ff,B_ff,a_next,b_next;
    always_comb begin
        a_next=A_ff; b_next=B_ff;
        case(controlpath_state)
            0: begin a_next=(A==0) ? B : A; b_next=(B==0) ? A : B; end
            2: a_next=A_ff-B_ff;
            3: b_next=B_ff-A_ff;
            default: begin end
        endcase
    end
    assign equal=a_next==b_next;
    assign greater_than=a_next>b_next;
    always_ff @(posedge clk) begin
        if(rst) begin A_ff<=0; B_ff<=0; OUT<=0; end
        else if(controlpath_state==1) OUT<=A_ff;
        else if(controlpath_state!=0 || go) begin A_ff<=a_next; B_ff<=b_next; end
    end
endmodule
